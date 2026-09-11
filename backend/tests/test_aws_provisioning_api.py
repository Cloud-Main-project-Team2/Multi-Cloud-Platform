"""API 명세서 v1.1 §10 프로비저닝 API 검증 (AWS EC2, `app/aws_provisioning.py`).

HTTP 계층 테스트는 `_run_provisioning_job`을 monkeypatch로 no-op화해서 API 계약(job 생성,
헤더/소유권/시크릿 필드 검증, idempotency, 취소)만 결정적으로 검증한다. 백그라운드 실행 로직은
`_execute_job()`을 `db_session`으로 직접 호출해서 검증한다 — `_run_provisioning_job`이 여는
`SessionLocal()`은 테스트의 트랜잭션 롤백과 무관한 별도 커넥션이라 API 테스트에서는 절대
실행시키지 않는다(test_sync_jobs_api.py와 같은 패턴).
"""

from __future__ import annotations

import pytest

import app.routers.provisioning as provisioning_router
from app.models import CloudAccount, Credential, Notification, ProvisioningJob, Resource, ServiceCatalog
from app.security.credential_crypto import encrypt_credential_json
from app.terraform_runner import TerraformResult

VALID_PROVIDER_SPEC = {"region": "ap-northeast-2", "instance_type": "t3.micro"}
VALID_COMMON_SPEC = {"name": "web-01"}
HEADERS_BASE = {"Idempotency-Key": "test-key-1", "X-Action-Confirmed": "true"}


def _make_service(db_session, provider, service_code, provisionable=True, category="compute", display_name=None):
    row = db_session.query(ServiceCatalog).filter_by(provider=provider, service_code=service_code).one_or_none()
    if row is not None:
        return row
    row = ServiceCatalog(
        provider=provider,
        service_code=service_code,
        category=category,
        display_name=display_name or service_code,
        provisionable=provisionable,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user, provider, external_account_id, label=None):
    row = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id, account_label=label)
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account, name="cred", payload=None, verified=True):
    ciphertext, nonce = encrypt_credential_json(payload or {"access_key_id": "AKIAFAKE", "secret_access_key": "fake-secret"})
    row = Credential(
        cloud_account_id=account.id, name=name, encrypted_payload=ciphertext, encryption_nonce=nonce,
        encryption_key_version="v1", verified=verified,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture(autouse=True)
def _noop_background_provisioning(monkeypatch):
    """HTTP 계층 테스트에서는 실제 백그라운드 job이 절대 돌지 않게 막는다(위 모듈 설명 참고)."""
    monkeypatch.setattr(provisioning_router, "_run_provisioning_job", lambda *a, **k: None)


def _setup(db_session, user, provider="aws", service_code="ec2"):
    service = _make_service(db_session, provider, service_code)
    account = _make_account(db_session, user, provider, "111122223333")
    credential = _make_credential(db_session, account)
    db_session.commit()
    return service, account, credential


# --- POST /provisioning/{provider}/{service} ----------------------------------------------


def test_create_job_returns_202_queued(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), **HEADERS_BASE},
    )

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["status"] == "queued"
    assert data["status_url"] == f"/api/v1/provisioning/jobs/{data['id']}"

    job = db_session.query(ProvisioningJob).filter_by(id=int(data["id"])).one()
    assert job.workspace_name == f"user-{user.id}-job-{job.id}"
    assert job.spec_json == {"common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC}


def test_create_job_requires_auth(client, db_session):
    resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": "1", "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers=HEADERS_BASE,
    )
    assert resp.status_code == 401


def test_create_job_requires_idempotency_key(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_create_job_requires_confirmation_header(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), "Idempotency-Key": "k1"},
    )
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_create_job_unknown_service_is_404(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/aws/does-not-exist",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), **HEADERS_BASE},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SERVICE_NOT_FOUND"


def test_create_job_not_provisionable_service_is_422(client, make_user, auth_header, db_session):
    user = make_user()
    _make_service(db_session, "aws", "rds", provisionable=False)
    account = _make_account(db_session, user, "aws", "111122223333")
    credential = _make_credential(db_session, account)
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/aws/rds",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), **HEADERS_BASE},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_PROVISIONABLE"


def test_create_job_without_runner_is_501(client, make_user, auth_header, db_session):
    user = make_user()
    # provisionable하지만 러너가 등록되지 않은 조합(gcp/cloud_storage). ec2/vm/compute_engine
    # 세 조합은 통합 후 모두 러너가 있으므로 러너 없는 서비스로 검증한다.
    _make_service(db_session, "gcp", "cloud_storage", provisionable=True)
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/cloud_storage",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), **HEADERS_BASE},
    )
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "PROVISIONING_NOT_IMPLEMENTED"


@pytest.mark.parametrize(
    "spec_key,bad_value",
    [("common_spec", {"name": "Bad_Name!"}), ("provider_spec", {"region": "ap-northeast-2", "instance_type": "m5.24xlarge"})],
)
def test_create_job_rejects_invalid_spec_values(client, make_user, auth_header, db_session, spec_key, bad_value):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)
    payload = {"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC}
    payload[spec_key] = bad_value

    resp = client.post(
        "/api/v1/provisioning/aws/ec2", json=payload, headers={**auth_header(user), **HEADERS_BASE}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("field", ["common_spec", "provider_spec"])
def test_create_job_rejects_secret_like_fields(client, make_user, auth_header, db_session, field):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)
    payload = {"credential_id": str(credential.id), "common_spec": dict(VALID_COMMON_SPEC), "provider_spec": dict(VALID_PROVIDER_SPEC)}
    payload[field]["secret_access_key"] = "leak-me"

    resp = client.post(
        "/api/v1/provisioning/aws/ec2", json=payload, headers={**auth_header(user), **HEADERS_BASE}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "SECRET_FIELD_NOT_ALLOWED"


def test_create_job_rejects_other_users_credential(client, make_user, auth_header, db_session):
    owner = make_user(email="owner@example.com")
    other = make_user(email="other@example.com")
    _service, _account, credential = _setup(db_session, owner)

    resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(other), **HEADERS_BASE},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


def test_create_job_rejects_credential_from_other_provider(client, make_user, auth_header, db_session):
    user = make_user()
    _make_service(db_session, "aws", "ec2")
    azure_account = _make_account(db_session, user, "azure", "sub-1")
    azure_credential = _make_credential(db_session, azure_account)
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(azure_credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), **HEADERS_BASE},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


def test_create_job_idempotent_replay_returns_same_job(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)
    payload = {"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC}
    headers = {**auth_header(user), **HEADERS_BASE}

    first = client.post("/api/v1/provisioning/aws/ec2", json=payload, headers=headers)
    second = client.post("/api/v1/provisioning/aws/ec2", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert db_session.query(ProvisioningJob).filter_by(user_id=user.id).count() == 1


def test_create_job_idempotency_key_reused_with_different_payload_conflicts(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)
    headers = {**auth_header(user), **HEADERS_BASE}

    client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers=headers,
    )
    resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": {"name": "web-02"}, "provider_spec": VALID_PROVIDER_SPEC},
        headers=headers,
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


# --- GET /provisioning/jobs, GET .../{id}, POST .../cancel ---------------------------------


def test_list_jobs_returns_only_own(client, make_user, auth_header, db_session):
    owner = make_user(email="owner2@example.com")
    other = make_user(email="other2@example.com")
    _service, _account, owner_cred = _setup(db_session, owner)
    _service2, _account2, other_cred = _setup(db_session, other)
    headers_owner = {**auth_header(owner), **HEADERS_BASE}
    headers_other = {**auth_header(other), "Idempotency-Key": "other-key", "X-Action-Confirmed": "true"}

    client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(owner_cred.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers=headers_owner,
    )
    client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(other_cred.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers=headers_other,
    )

    resp = client.get("/api/v1/provisioning/jobs", headers=auth_header(owner))
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] == 1


def test_get_job_404_for_other_user(client, make_user, auth_header, db_session):
    owner = make_user(email="owner3@example.com")
    other = make_user(email="other3@example.com")
    _service, _account, credential = _setup(db_session, owner)

    create_resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(owner), **HEADERS_BASE},
    )
    job_id = create_resp.json()["data"]["id"]

    resp = client.get(f"/api/v1/provisioning/jobs/{job_id}", headers=auth_header(other))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PROVISIONING_JOB_NOT_FOUND"


def test_cancel_queued_job_marks_cancelled_immediately(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    create_resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), **HEADERS_BASE},
    )
    job_id = create_resp.json()["data"]["id"]

    resp = client.post(f"/api/v1/provisioning/jobs/{job_id}/cancel", headers=auth_header(user))
    assert resp.status_code == 202
    assert resp.json()["data"]["status"] == "cancelled"


def test_cancel_terminal_job_is_rejected(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)
    create_resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        json={"credential_id": str(credential.id), "common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        headers={**auth_header(user), **HEADERS_BASE},
    )
    job_id = create_resp.json()["data"]["id"]
    db_session.query(ProvisioningJob).filter_by(id=int(job_id)).update({"status": "success"})
    db_session.commit()

    resp = client.post(f"/api/v1/provisioning/jobs/{job_id}/cancel", headers=auth_header(user))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "JOB_NOT_CANCELLABLE"


# --- _execute_job() (백그라운드 실행 로직, db_session으로 직접 호출) -------------------------


def _create_queued_job(db_session, user, credential, service):
    job = ProvisioningJob(
        user_id=user.id,
        credential_id=credential.id,
        service_catalog_id=service.id,
        workspace_name="pending",
        idempotency_key="exec-test-key",
        spec_json={"common_spec": VALID_COMMON_SPEC, "provider_spec": VALID_PROVIDER_SPEC},
        status="queued",
    )
    db_session.add(job)
    db_session.flush()
    job.workspace_name = f"user-{user.id}-job-{job.id}"
    db_session.commit()
    db_session.refresh(job)
    return job


def test_execute_job_success_creates_resource_and_notification(monkeypatch, make_user, db_session):
    user = make_user()
    service, account, credential = _setup(db_session, user)
    job = _create_queued_job(db_session, user, credential, service)

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type(
            "R", (), {"run": staticmethod(lambda **kwargs: TerraformResult(success=True, outputs={"instance_id": "i-abc123"}))}
        ),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert job.status == "success"
    assert job.progress_percent == 100
    assert job.result_json == {"instance_id": "i-abc123"}

    resource = db_session.query(Resource).filter_by(cloud_account_id=account.id).one()
    assert resource.external_resource_id == "i-abc123"
    assert resource.provider_resource_key == "aws:ec2:i-abc123"

    notification = db_session.query(Notification).filter_by(user_id=user.id).one()
    assert notification.type == "provisioning_succeeded"
    assert notification.message_key == "notif.provisioning.succeeded"


def test_execute_job_failure_records_error_without_resource(monkeypatch, make_user, db_session):
    user = make_user()
    service, account, credential = _setup(db_session, user)
    job = _create_queued_job(db_session, user, credential, service)

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type(
            "R",
            (),
            {"run": staticmethod(lambda **kwargs: TerraformResult(success=False, error_code="TERRAFORM_ERROR", error_message="boom"))},
        ),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "TERRAFORM_ERROR"
    assert db_session.query(Resource).filter_by(cloud_account_id=account.id).count() == 0


def test_execute_job_already_cancel_requested_skips_run(monkeypatch, make_user, db_session):
    user = make_user()
    service, _account, credential = _setup(db_session, user)
    job = _create_queued_job(db_session, user, credential, service)

    called = {"run": False}
    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type("R", (), {"run": staticmethod(lambda **kwargs: called.__setitem__("run", True))}),
    )
    provisioning_router._CANCEL_REQUESTED.add(job.id)
    try:
        provisioning_router._execute_job(db_session, job)
    finally:
        provisioning_router._CANCEL_REQUESTED.discard(job.id)

    db_session.refresh(job)
    assert job.status == "cancelled"
    assert called["run"] is False


def test_execute_job_rejects_unverified_credential_without_calling_runner(monkeypatch, make_user, db_session):
    user = make_user()
    service = _make_service(db_session, "aws", "ec2")
    account = _make_account(db_session, user, "aws", "111122223333")
    credential = _make_credential(db_session, account, verified=False)
    db_session.commit()
    job = _create_queued_job(db_session, user, credential, service)

    called = {"run": False}
    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type("R", (), {"run": staticmethod(lambda **kwargs: called.__setitem__("run", True))}),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "CLOUD_PERMISSION_DENIED"
    assert called["run"] is False
    assert db_session.query(Notification).filter_by(user_id=user.id).count() == 1


def test_execute_job_proceeds_despite_provision_scope_false(monkeypatch, make_user, db_session):
    # provision=false는 사전 차단하지 않는다 — iam:SimulatePrincipalPolicy 프로빙은 EC2 전용 키에서
    # false negative가 잦아, 실제 권한 게이트는 Terraform apply로 둔다.
    user = make_user()
    service, _account, credential = _setup(db_session, user)
    credential.permission_scope = {"provision": False, "inventory_read": True}
    db_session.commit()
    job = _create_queued_job(db_session, user, credential, service)

    called = {"run": False}

    def _run(**kwargs):
        called["run"] = True
        return TerraformResult(success=True, outputs={"instance_id": "i-abc123"})

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type("R", (), {"run": staticmethod(_run)}),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert called["run"] is True
    assert job.status == "success"
