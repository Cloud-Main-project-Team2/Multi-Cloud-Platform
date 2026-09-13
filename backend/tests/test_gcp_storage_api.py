"""API 명세서 v1.1 §10 프로비저닝 API 검증 (GCP Cloud Storage, `app/gcp_storage_provisioning.py`).

일반 계약(헤더/소유권/멱등성/취소)은 `test_provisioning_api.py`(gcp/compute_engine 기준)가 이미
검증한다. 여기서는 Cloud Storage 고유의 것만 다룬다: 버킷 이름이 mcp- 접두사 없이 그대로
쓰이는지, storage_class 허용 목록, `_execute_job()`이 Compute/Cloud SQL과 다른 리소스 유형
("Cloud Storage Bucket")으로 리소스행을 만드는지.
"""

from __future__ import annotations

import pytest

import app.routers.provisioning as provisioning_router
from app.models import CloudAccount, Credential, ProvisioningJob, Resource, ServiceCatalog
from app.security.credential_crypto import encrypt_credential_json
from app.terraform_runner import TerraformResult

VALID_PROVIDER_SPEC = {"region": "asia-northeast3", "storage_class": "Standard"}
VALID_COMMON_SPEC = {"name": "my-unique-bucket-01"}
_HEADERS = {"Idempotency-Key": "test-key-1", "X-Action-Confirmed": "true"}


def _make_service(db_session, provider="gcp", service_code="cloud_storage", provisionable=True):
    row = db_session.query(ServiceCatalog).filter_by(provider=provider, service_code=service_code).one_or_none()
    if row is not None:
        return row
    row = ServiceCatalog(
        provider=provider, service_code=service_code, category="storage_object", display_name="Cloud Storage",
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


# --- POST /provisioning/gcp/cloud_storage ---------------------------------------------------


def test_create_job_success_returns_202(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/gcp/cloud_storage", json=_body(credential.id), headers={**auth_header(user), **_HEADERS}
    )

    assert resp.status_code == 202
    assert resp.json()["data"]["status"] == "queued"


def test_create_job_rejects_invalid_storage_class(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/gcp/cloud_storage", json=_body(credential.id, storage_class="Glacier"),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_job_rejects_reserved_bucket_name(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    body = _body(credential.id)
    body["common_spec"] = {"name": "google-reserved-bucket"}

    resp = client.post(
        "/api/v1/provisioning/gcp/cloud_storage", json=body, headers={**auth_header(user), **_HEADERS}
    )

    assert resp.status_code == 422


# --- _execute_job(): Cloud Storage 리소스행 생성 ---------------------------------------------


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


def test_execute_job_success_creates_storage_resource_with_distinct_type_and_no_prefix(monkeypatch, make_user, db_session):
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
                        outputs={"bucket_name": "my-unique-bucket-01", "region": "asia-northeast3"},
                    )
                )
            },
        ),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert job.status == "success"

    resource = db_session.query(Resource).filter_by(cloud_account_id=account.id).one()
    # mcp- 접두사가 붙지 않는다 — Compute/Cloud SQL과 다르게 버킷 이름은 사용자가 입력한 그대로.
    assert resource.external_resource_id == "my-unique-bucket-01"
    assert resource.original_resource_type == "Cloud Storage Bucket"
    assert resource.provider_resource_key == "gcp:cloud_storage:my-unique-bucket-01"
    assert resource.region == "asia-northeast3"
