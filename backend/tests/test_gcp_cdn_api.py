"""API 명세서 v1.1 §10 프로비저닝 API 검증 (GCP Cloud CDN, `app/gcp_cdn_provisioning.py`).

일반 계약(헤더/소유권/멱등성/취소)은 `test_provisioning_api.py`(gcp/compute_engine 기준)가 이미
검증한다. 여기서는 Cloud CDN 고유의 것만 다룬다: `lb_stack_ack` 필수, `_execute_job()`이
Compute/Cloud SQL/Cloud Storage와 다른 리소스 유형("Cloud CDN (HTTP LB)")·region=None으로
리소스행을 만드는지, 초기 상태가 "DEPLOYED"인지, 백엔드 버킷 자동 생성(`create_bucket`, 기본
True)일 때만 연결된 "Cloud Storage Bucket" 인벤토리 행이 함께 만들어지는지(기존 버킷을 쓰는
경우는 만들지 않음).
"""

from __future__ import annotations

import pytest

import app.routers.provisioning as provisioning_router
from app.models import CloudAccount, Credential, ProvisioningJob, Resource, ServiceCatalog
from app.security.credential_crypto import encrypt_credential_json
from app.terraform_runner import TerraformResult

VALID_PROVIDER_SPEC = {"lb_stack_ack": True}
VALID_COMMON_SPEC = {"name": "cdn-01"}
_HEADERS = {"Idempotency-Key": "test-key-1", "X-Action-Confirmed": "true"}


def _make_service(
    db_session, provider="gcp", service_code="cloud_cdn", provisionable=True,
    category="cdn", display_name="Cloud CDN",
):
    row = db_session.query(ServiceCatalog).filter_by(provider=provider, service_code=service_code).one_or_none()
    if row is not None:
        return row
    row = ServiceCatalog(
        provider=provider, service_code=service_code, category=category, display_name=display_name,
        provisionable=provisionable,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user, external_account_id="proj-1"):
    row = CloudAccount(user_id=user.id, provider="gcp", external_account_id=external_account_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account, verified=True, permission_scope=None):
    ciphertext, nonce = encrypt_credential_json({"type": "service_account", "token_uri": "https://oauth2.googleapis.com/token"})
    row = Credential(
        cloud_account_id=account.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
        encryption_key_version="v1", verified=verified, permission_scope=permission_scope or {},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _setup(db_session, user):
    service = _make_service(db_session)
    account = _make_account(db_session, user)
    credential = _make_credential(db_session, account)
    db_session.commit()
    return service, account, credential


def _body(credential_id, **provider_overrides):
    return {
        "credential_id": str(credential_id),
        "common_spec": VALID_COMMON_SPEC,
        "provider_spec": {**VALID_PROVIDER_SPEC, **provider_overrides},
    }


@pytest.fixture(autouse=True)
def _noop_background_job(monkeypatch):
    monkeypatch.setattr(provisioning_router, "_run_provisioning_job", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _cancel_set_isolation():
    provisioning_router._CANCEL_REQUESTED.clear()
    yield
    provisioning_router._CANCEL_REQUESTED.clear()


# --- POST /provisioning/gcp/cloud_cdn -------------------------------------------------------


def test_create_job_success_returns_202(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/gcp/cloud_cdn", json=_body(credential.id), headers={**auth_header(user), **_HEADERS}
    )

    assert resp.status_code == 202
    assert resp.json()["data"]["status"] == "queued"


def test_create_job_rejects_missing_lb_stack_ack(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/gcp/cloud_cdn", json=_body(credential.id, lb_stack_ack=False),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_job_success_with_existing_bucket_choice(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/gcp/cloud_cdn",
        json=_body(
            credential.id,
            create_bucket=False,
            backend_bucket_name="my-existing-bucket",
            existing_bucket_public_ack=True,
        ),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 202


def test_create_job_rejects_existing_bucket_without_public_ack(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/gcp/cloud_cdn",
        json=_body(credential.id, create_bucket=False, backend_bucket_name="my-existing-bucket"),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# --- _execute_job(): Cloud CDN 리소스행 생성 -------------------------------------------------


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


def test_execute_job_success_creates_cdn_resource_with_distinct_type_and_no_region(monkeypatch, make_user, db_session):
    user = make_user()
    service, account, credential = _setup(db_session, user)
    job = _create_queued_job(db_session, user, credential, service)

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type(
            "R",
            (),
            {
                "run": staticmethod(
                    lambda **kwargs: TerraformResult(
                        success=True,
                        outputs={"forwarding_rule_name": "mcp-cdn-01-fwd-rule", "ip_address": "34.1.2.3"},
                    )
                )
            },
        ),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert job.status == "success"

    resource = db_session.query(Resource).filter_by(cloud_account_id=account.id).one()
    assert resource.external_resource_id == "mcp-cdn-01-fwd-rule"
    assert resource.original_resource_type == "Cloud CDN (HTTP LB)"
    assert resource.provider_resource_key == "gcp:cloud_cdn:mcp-cdn-01-fwd-rule"
    assert resource.region is None
    assert resource.status == "DEPLOYED"


def test_execute_job_success_also_creates_linked_storage_bucket_resource(monkeypatch, make_user, db_session):
    """CDN 전용 버킷(2026-09-14 추가)도 인벤토리에서 보여야 한다 — 실사용 중 "안 보여서 관리가
    어렵다"는 지적으로 발견. (gcp, cloud_storage) service_catalog가 있어야 Storage로 분류된다."""
    user = make_user()
    service, account, credential = _setup(db_session, user)
    storage_service = _make_service(
        db_session, service_code="cloud_storage", category="storage_object", display_name="Cloud Storage",
    )
    job = _create_queued_job(db_session, user, credential, service)

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type(
            "R",
            (),
            {
                "run": staticmethod(
                    lambda **kwargs: TerraformResult(
                        success=True,
                        outputs={
                            "forwarding_rule_name": "mcp-cdn-01-fwd-rule",
                            "ip_address": "34.1.2.3",
                            "backend_bucket_name": "mcp-cdn-1",
                        },
                    )
                )
            },
        ),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert job.status == "success"
    assert job.created_resource_count == 2

    bucket_resource = (
        db_session.query(Resource)
        .filter_by(cloud_account_id=account.id, service_catalog_id=storage_service.id)
        .one()
    )
    assert bucket_resource.external_resource_id == "mcp-cdn-1"
    assert bucket_resource.original_resource_type == "Cloud Storage Bucket"
    assert bucket_resource.provider_resource_key == "gcp:cloud_storage:mcp-cdn-1"
    assert bucket_resource.tags["created-for"] == "cdn"

    cdn_resource = (
        db_session.query(Resource)
        .filter_by(cloud_account_id=account.id, service_catalog_id=service.id)
        .one()
    )
    assert cdn_resource.external_resource_id == "mcp-cdn-01-fwd-rule"


def test_execute_job_existing_bucket_does_not_create_linked_storage_resource(monkeypatch, make_user, db_session):
    """`create_bucket=False`(기존 버킷 사용)면 그 버킷은 우리가 만든 게 아니므로 소유를 주장하는
    "Cloud Storage Bucket" 행을 새로 만들지 않는다 — terraform output의
    `backend_bucket_created=False`로 판별한다."""
    user = make_user()
    service, account, credential = _setup(db_session, user)
    _make_service(
        db_session, service_code="cloud_storage", category="storage_object", display_name="Cloud Storage",
    )
    job = _create_queued_job(db_session, user, credential, service)

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type(
            "R",
            (),
            {
                "run": staticmethod(
                    lambda **kwargs: TerraformResult(
                        success=True,
                        outputs={
                            "forwarding_rule_name": "mcp-cdn-01-fwd-rule",
                            "ip_address": "34.1.2.3",
                            "backend_bucket_name": "my-existing-bucket",
                            "backend_bucket_created": False,
                        },
                    )
                )
            },
        ),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert job.status == "success"
    assert job.created_resource_count == 1

    bucket_rows = (
        db_session.query(Resource)
        .filter_by(cloud_account_id=account.id, provider_resource_key="gcp:cloud_storage:my-existing-bucket")
        .all()
    )
    assert bucket_rows == []
