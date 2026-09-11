"""API 명세서 v1.1 §10 프로비저닝 API 검증.

`test_sync_jobs_api.py`와 같은 이유로 HTTP 계층 테스트는 백그라운드 실행 함수
(`_run_provisioning_job`)를 monkeypatch로 no-op화해서 API 계약(202/404/422/409/428, 멱등성,
소유권)만 결정적으로 검증한다. 실제 job 처리 로직(`_execute_job`)은
`app.provisioning.get_runner()`를 monkeypatch해서 `db_session`으로 직접 호출해 검증한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

import app.routers.provisioning as provisioning_router
from app.models import CloudAccount, Credential, Notification, ProvisioningJob, Resource, ServiceCatalog
from app.security.credential_crypto import encrypt_credential_json
from app.terraform_runner import TerraformResult

_HEADERS = {"X-Action-Confirmed": "true", "Idempotency-Key": "test-key-1"}


def _make_service(db_session, provider, service_code, category="compute", provisionable=True):
    row = db_session.query(ServiceCatalog).filter_by(provider=provider, service_code=service_code).one_or_none()
    if row is not None:
        return row
    row = ServiceCatalog(
        provider=provider, service_code=service_code, category=category, display_name=service_code,
        provisionable=provisionable,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user, provider, external_account_id):
    row = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account, name="cred", verified=True, permission_scope=None):
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    row = Credential(
        cloud_account_id=account.id, name=name, encrypted_payload=ciphertext, encryption_nonce=nonce,
        encryption_key_version="v1", verified=verified, permission_scope=permission_scope or {},
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture(autouse=True)
def _noop_background_job(monkeypatch):
    monkeypatch.setattr(provisioning_router, "_run_provisioning_job", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _cancel_set_isolation():
    provisioning_router._CANCEL_REQUESTED.clear()
    yield
    provisioning_router._CANCEL_REQUESTED.clear()


def _body(credential_id, name="web-01", region="asia-northeast3", instance_type="e2-micro"):
    return {
        "credential_id": str(credential_id),
        "common_spec": {"name": name},
        "provider_spec": {"region": region, "instance_type": instance_type},
    }


# --- POST /provisioning/{provider}/{service} -----------------------------------------------


def test_create_job_requires_confirmation_header(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine",
        json=_body(credential.id),
        headers={**auth_header(user), "Idempotency-Key": "k1"},
    )

    assert resp.status_code == 428


def test_create_job_requires_idempotency_key(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine",
        json=_body(credential.id),
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_create_job_service_not_found(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SERVICE_NOT_FOUND"


def test_create_job_rejects_non_provisionable_service(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine", provisionable=False)
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 422


def test_create_job_returns_501_when_no_runner_registered(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    credential = _make_credential(db_session, account)
    # provisionable하지만 러너가 등록되지 않은 조합(aws/rds) — ec2/vm/compute_engine 세 조합은
    # 통합 후 모두 러너가 있으므로 러너 없는 서비스로 검증한다.
    _make_service(db_session, "aws", "rds")
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/aws/rds",
        json={"credential_id": str(credential.id), "common_spec": {"name": "web-01"}, "provider_spec": {}},
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "PROVISIONING_NOT_IMPLEMENTED"


def test_create_job_rejects_secret_field_in_spec(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    body = _body(credential.id)
    body["provider_spec"]["private_key"] = "leaked"

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine", json=body, headers={**auth_header(user), **_HEADERS}
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "SECRET_FIELD_NOT_ALLOWED"


def test_create_job_rejects_foreign_credential(client, make_user, auth_header, db_session):
    owner = make_user(email="owner@example.com")
    intruder = make_user(email="intruder@example.com")
    account = _make_account(db_session, owner, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id),
        headers={**auth_header(intruder), **_HEADERS},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


def test_create_job_rejects_credential_provider_mismatch(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id),
        headers={**auth_header(user), **_HEADERS},
    )

    # 소유 credential이지만 provider가 달라 존재를 드러내지 않고 404로 처리한다(통합 계약).
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


def test_create_job_rejects_invalid_spec(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine",
        json=_body(credential.id, region="not-a-region"),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 422


def test_create_job_success_returns_202_with_status_url(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    resp = client.post(
        "/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["status"] == "queued"
    assert data["status_url"] == f"/api/v1/provisioning/jobs/{data['id']}"

    job = db_session.get(ProvisioningJob, int(data["id"]))
    assert job.workspace_name == f"user-{user.id}-job-{job.id}"
    assert job.spec_json["common_spec"]["name"] == "web-01"


def test_create_job_idempotency_key_reuse_returns_same_job(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    headers = {**auth_header(user), **_HEADERS}
    first = client.post("/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id), headers=headers)
    second = client.post("/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["id"] == second.json()["data"]["id"]

    jobs = db_session.query(ProvisioningJob).filter_by(user_id=user.id).all()
    assert len(jobs) == 1


def test_create_job_idempotency_key_reuse_with_different_payload_conflicts(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    _make_service(db_session, "gcp", "compute_engine")
    db_session.commit()

    headers = {**auth_header(user), **_HEADERS}
    first = client.post("/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id, name="web-01"), headers=headers)
    second = client.post("/api/v1/provisioning/gcp/compute_engine", json=_body(credential.id, name="web-02"), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


# --- GET /provisioning/jobs, /provisioning/jobs/{id} ----------------------------------------


def test_list_jobs_only_returns_own(client, make_user, auth_header, db_session):
    owner = make_user(email="owner2@example.com")
    other = make_user(email="other2@example.com")
    account = _make_account(db_session, owner, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    service = _make_service(db_session, "gcp", "compute_engine")
    db_session.add(ProvisioningJob(
        user_id=owner.id, credential_id=credential.id, service_catalog_id=service.id,
        workspace_name="ws-1", idempotency_key="k-1", spec_json={}, status="queued",
    ))
    other_account = _make_account(db_session, other, "gcp", "proj-2")
    other_credential = _make_credential(db_session, other_account)
    db_session.add(ProvisioningJob(
        user_id=other.id, credential_id=other_credential.id, service_catalog_id=service.id,
        workspace_name="ws-2", idempotency_key="k-2", spec_json={}, status="queued",
    ))
    db_session.commit()

    resp = client.get("/api/v1/provisioning/jobs", headers=auth_header(owner))

    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 1


def test_get_job_not_found_for_other_user(client, make_user, auth_header, db_session):
    owner = make_user(email="owner3@example.com")
    intruder = make_user(email="intruder3@example.com")
    account = _make_account(db_session, owner, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = ProvisioningJob(
        user_id=owner.id, credential_id=credential.id, service_catalog_id=service.id,
        workspace_name="ws-3", idempotency_key="k-3", spec_json={}, status="queued",
    )
    db_session.add(job)
    db_session.commit()

    resp = client.get(f"/api/v1/provisioning/jobs/{job.id}", headers=auth_header(intruder))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PROVISIONING_JOB_NOT_FOUND"


def test_get_job_detail_serializes_spec_and_error(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = ProvisioningJob(
        user_id=user.id, credential_id=credential.id, service_catalog_id=service.id,
        workspace_name="ws-4", idempotency_key="k-4",
        spec_json={"common_spec": {"name": "web-01"}, "provider_spec": {"region": "asia-northeast3"}},
        status="failed", error_code="TERRAFORM_ERROR", error_message="boom",
    )
    db_session.add(job)
    db_session.commit()

    resp = client.get(f"/api/v1/provisioning/jobs/{job.id}", headers=auth_header(user))

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["common_spec"] == {"name": "web-01"}
    assert data["provider_spec"] == {"region": "asia-northeast3"}
    assert data["error"] == {"code": "TERRAFORM_ERROR", "message": "boom"}


# --- POST /provisioning/jobs/{id}/cancel ----------------------------------------------------


def test_cancel_queued_job_marks_cancelled_immediately(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = ProvisioningJob(
        user_id=user.id, credential_id=credential.id, service_catalog_id=service.id,
        workspace_name="ws-5", idempotency_key="k-5", spec_json={}, status="queued",
    )
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/api/v1/provisioning/jobs/{job.id}/cancel", headers=auth_header(user))

    assert resp.status_code == 202
    assert resp.json()["data"]["status"] == "cancelled"


def test_cancel_running_job_is_best_effort_flagged(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = ProvisioningJob(
        user_id=user.id, credential_id=credential.id, service_catalog_id=service.id,
        workspace_name="ws-6", idempotency_key="k-6", spec_json={}, status="running",
    )
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/api/v1/provisioning/jobs/{job.id}/cancel", headers=auth_header(user))

    assert resp.status_code == 202
    assert job.id in provisioning_router._CANCEL_REQUESTED


def test_cancel_terminal_job_is_rejected(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = ProvisioningJob(
        user_id=user.id, credential_id=credential.id, service_catalog_id=service.id,
        workspace_name="ws-7", idempotency_key="k-7", spec_json={}, status="success",
    )
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/api/v1/provisioning/jobs/{job.id}/cancel", headers=auth_header(user))

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "JOB_NOT_CANCELLABLE"


# --- 프로비저닝 실행 로직(직접 호출, DB 세션 공유) --------------------------------------------


def _pending_job(db_session, user, credential, service, **spec):
    job = ProvisioningJob(
        user_id=user.id, credential_id=credential.id, service_catalog_id=service.id,
        workspace_name=f"user-{user.id}-job-pending", idempotency_key="k-run",
        spec_json=spec or {"common_spec": {"name": "web-01"}, "provider_spec": {"region": "asia-northeast3", "instance_type": "e2-micro"}},
        status="running",
    )
    db_session.add(job)
    db_session.flush()
    return job


def test_process_job_fails_without_verified_credential(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account, verified=False)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = _pending_job(db_session, user, credential, service)
    db_session.commit()

    provisioning_router._execute_job(db_session, job)

    assert job.status == "failed"
    assert job.error_code == "CLOUD_PERMISSION_DENIED"


def test_process_job_proceeds_despite_provision_scope_false(db_session, make_user, monkeypatch):
    # permission_scope.provision은 iam:SimulatePrincipalPolicy 기반이라 EC2 권한만 있는 키 등에서
    # false negative가 잦다 — 사전 차단하지 않고 러너(Terraform)로 진행한다. 진짜 권한이 없으면
    # apply가 CLOUD_PERMISSION_DENIED로 실패한다.
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account, verified=True, permission_scope={
        "inventory_read": True, "resource_control": True, "provision": False, "cost_read": False,
    })
    service = _make_service(db_session, "gcp", "compute_engine")
    job = _pending_job(db_session, user, credential, service)
    db_session.commit()

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, code: type("R", (), {
            "run": staticmethod(lambda **kwargs: TerraformResult(
                success=True, outputs={"instance_name": "mcp-web-01", "zone": "asia-northeast3-a"}
            ))
        })(),
    )

    provisioning_router._execute_job(db_session, job)

    assert job.status == "success"


def test_process_job_success_creates_resource_and_notification(db_session, make_user, monkeypatch):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account, verified=True)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = _pending_job(db_session, user, credential, service)
    db_session.commit()

    fake_outputs = {"instance_name": "mcp-web-01", "zone": "asia-northeast3-a", "external_ip": "1.2.3.4"}
    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, code: type("R", (), {
            "run": staticmethod(lambda **kwargs: TerraformResult(success=True, outputs=fake_outputs))
        })(),
    )

    provisioning_router._execute_job(db_session, job)

    assert job.status == "success"
    assert job.progress_percent == 100
    assert job.created_resource_count == 1

    resource = db_session.query(Resource).filter_by(cloud_account_id=account.id).one()
    assert resource.external_resource_id == "mcp-web-01"
    assert resource.region == "asia-northeast3"
    assert resource.status == "RUNNING"

    notification = db_session.query(Notification).filter_by(reference_id=job.id).one()
    assert notification.type == "provisioning_succeeded"
    assert notification.message_key == "notif.provisioning.succeeded"


def test_process_job_failure_records_error_and_notification(db_session, make_user, monkeypatch):
    user = make_user()
    account = _make_account(db_session, user, "gcp", "proj-1")
    credential = _make_credential(db_session, account, verified=True)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = _pending_job(db_session, user, credential, service)
    db_session.commit()

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, code: type("R", (), {
            "run": staticmethod(lambda **kwargs: TerraformResult(
                success=False, error_code="QUOTA_EXCEEDED", error_message="quota exceeded"
            ))
        })(),
    )

    provisioning_router._execute_job(db_session, job)

    assert job.status == "failed"
    assert job.error_code == "QUOTA_EXCEEDED"

    notification = db_session.query(Notification).filter_by(reference_id=job.id).one()
    assert notification.type == "provisioning_failed"
    assert notification.message_params["reason"] == "quota exceeded"
