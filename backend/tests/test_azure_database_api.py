"""API 명세서 v1.1 §10 프로비저닝 API 검증 (Azure Database, `app/azure_database_provisioning.py`).

일반 계약(헤더/소유권/멱등성/취소)은 `test_provisioning_api.py`류가 이미 검증한다. 여기서는
Azure Database 고유의 것만 다룬다: engine 허용 목록, master_password가 secret-필드 금지 검사를
통과하면서도 저장분에서는 제거되는지, `_execute_job()`이 엔진별로 다른 `original_resource_type`
으로 리소스행을 만드는지.
"""

from __future__ import annotations

import pytest

import app.routers.provisioning as provisioning_router
from app.models import CloudAccount, Credential, ProvisioningJob, Resource, ServiceCatalog
from app.security.credential_crypto import encrypt_credential_json
from app.terraform_runner import TerraformResult

VALID_PROVIDER_SPEC = {
    "region": "koreacentral",
    "engine": "MySQL",
    "master_username": "dbadmin",
    "master_password": "S3cure!Pass",
}
VALID_COMMON_SPEC = {"name": "web-db"}
_HEADERS = {"Idempotency-Key": "test-key-1", "X-Action-Confirmed": "true"}


def _make_service(db_session, provider="azure", service_code="sql_database", provisionable=True):
    row = db_session.query(ServiceCatalog).filter_by(provider=provider, service_code=service_code).one_or_none()
    if row is not None:
        return row
    row = ServiceCatalog(
        provider=provider, service_code=service_code, category="db_rdbms", display_name="SQL Database",
        provisionable=provisionable,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user, external_account_id="sub-1"):
    row = CloudAccount(user_id=user.id, provider="azure", external_account_id=external_account_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account, verified=True, permission_scope=None):
    ciphertext, nonce = encrypt_credential_json(
        {"tenant_id": "tenant-1", "client_id": "client-1", "client_secret": "supersecretvalue", "subscription_id": "sub-1"}
    )
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


# --- POST /provisioning/azure/sql_database -----------------------------------------------------


def test_create_job_success_returns_202(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/azure/sql_database", json=_body(credential.id),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 202
    assert resp.json()["data"]["status"] == "queued"


def test_create_job_rejects_invalid_engine(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/azure/sql_database", json=_body(credential.id, engine="Oracle"),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_job_rejects_reserved_master_username(client, make_user, auth_header, db_session):
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    resp = client.post(
        "/api/v1/provisioning/azure/sql_database", json=_body(credential.id, master_username="admin"),
        headers={**auth_header(user), **_HEADERS},
    )

    assert resp.status_code == 422


def test_master_password_allowed_and_never_persisted_or_returned(client, make_user, auth_header, db_session):
    """master_password는 이름에 "password"가 들어가지만 secret-필드 금지 예외 대상이다 —
    RDS/Cloud SQL/Azure Storage와 동일 정책. 요청은 통과하되 DB 저장분/조회 응답엔 안 남는다."""
    user = make_user()
    _service, _account, credential = _setup(db_session, user)

    create_resp = client.post(
        "/api/v1/provisioning/azure/sql_database", json=_body(credential.id), headers={**auth_header(user), **_HEADERS}
    )
    assert create_resp.status_code == 202
    job_id = create_resp.json()["data"]["id"]

    job = db_session.get(ProvisioningJob, int(job_id))
    assert "master_password" not in job.spec_json["provider_spec"]

    get_resp = client.get(f"/api/v1/provisioning/jobs/{job_id}", headers=auth_header(user))
    assert "master_password" not in get_resp.json()["data"]["provider_spec"]


# --- _execute_job(): 엔진별 리소스 타입 구분 -----------------------------------------------------


def _create_queued_job(db_session, user, credential, service, provider_spec=VALID_PROVIDER_SPEC):
    job = ProvisioningJob(
        user_id=user.id,
        credential_id=credential.id,
        service_catalog_id=service.id,
        workspace_name="pending",
        idempotency_key="exec-test-key",
        spec_json={"common_spec": VALID_COMMON_SPEC, "provider_spec": provider_spec},
        status="queued",
    )
    db_session.add(job)
    db_session.flush()
    job.workspace_name = f"user-{user.id}-job-{job.id}"
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.mark.parametrize(
    "db_engine,expected_type",
    [
        ("MySQL", "Azure Database for MySQL"),
        ("PostgreSQL", "Azure Database for PostgreSQL"),
        ("SQL Server", "Azure SQL Database"),
    ],
)
def test_execute_job_success_creates_resource_with_engine_specific_type(
    monkeypatch, make_user, db_session, db_engine, expected_type
):
    # 주의: 파라미터 이름을 "engine"으로 쓰면 conftest.py의 세션 스코프 SQLAlchemy `engine` 픽스처와
    # 충돌해 db_session이 문자열을 받아 깨진다 — 그래서 "db_engine"으로 이름 붙였다.
    user = make_user()
    service, account, credential = _setup(db_session, user)
    provider_spec = {**VALID_PROVIDER_SPEC, "engine": db_engine}
    job = _create_queued_job(db_session, user, credential, service, provider_spec=provider_spec)

    monkeypatch.setattr(
        provisioning_router,
        "get_runner",
        lambda provider, service_code: type(
            "R",
            (),
            {
                "run": staticmethod(
                    lambda **kwargs: TerraformResult(success=True, outputs={"server_name": f"mcp-web-db-{job.id}"})
                )
            },
        ),
    )

    provisioning_router._execute_job(db_session, job)

    db_session.refresh(job)
    assert job.status == "success"

    resource = db_session.query(Resource).filter_by(cloud_account_id=account.id).one()
    assert resource.original_resource_type == expected_type
    assert resource.status == "AVAILABLE"
    assert resource.region == "koreacentral"
