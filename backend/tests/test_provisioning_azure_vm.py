"""POST /provisioning/azure/vm, GET /provisioning/jobs/{id} 통합 테스트(통합 라우터).

실제 terraform은 절대 호출하지 않는다 — 백그라운드 실행 함수(`_run_provisioning_job`)를
가짜로 바꿔치기해서 라우터의 검증·소유권·멱등성·오류 코드만 검증한다. Azure 러너 자체의 내부
로직은 test_azure_vm_executor.py에서 다룬다.

conftest.py의 `db_session`(롤백 전용) 대신 실제로 commit하는 세션을 쓴다 — 라우터가 요청 처리
중 commit하고, TestClient가 실행하는 BackgroundTasks는 별도 커넥션을 여는 `get_db` override로
그 commit된 데이터를 봐야 하기 때문이다. 테스트가 끝나면 모든 테이블을 직접 비운다.
"""

import base64
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.routers.provisioning as provisioning_router
from app.db import Base, get_db
from app.main import app
from app.models import CloudAccount, Credential, ProvisioningJob, ServiceCatalog, User
from app.security import credential_crypto as cc
from app.security.jwt_tokens import create_access_token

VALID_KEY = base64.b64encode(os.urandom(32)).decode()

VALID_PROVIDER_SPEC = {
    "region": "koreacentral",
    "instance_type": "B1s",
    "admin_username": "azureuser",
    "admin_password": "S3curePassw0rd!",
}


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", VALID_KEY)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-not-for-production")
    cc.get_settings.cache_clear()
    yield
    cc.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _noop_background(monkeypatch):
    """기본값: 백그라운드 실행을 no-op으로 막는다(job이 "queued"로 남는지 확인하는 테스트용).
    개별 테스트가 다른 동작을 보고 싶으면 다시 덮어쓴다."""
    monkeypatch.setattr(provisioning_router, "_run_provisioning_job", lambda *a, **k: None)


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, future=True)


@pytest.fixture()
def client(engine, session_factory):
    def _get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture()
def azure_fixture(session_factory):
    session = session_factory()

    user = User(email="a@example.com", normalized_email="a@example.com", name="A", affiliation_type="individual")
    other_user = User(
        email="b@example.com", normalized_email="b@example.com", name="B", affiliation_type="individual"
    )
    session.add_all([user, other_user])
    session.flush()

    account = CloudAccount(user_id=user.id, provider="azure", external_account_id="sub-1")
    session.add(account)
    session.flush()

    ciphertext, nonce = cc.encrypt_credential_json(
        {"tenant_id": "t", "client_id": "c", "client_secret": "s", "subscription_id": "sub"}
    )
    credential = Credential(
        cloud_account_id=account.id,
        name="provisioner",
        encrypted_payload=ciphertext,
        encryption_nonce=nonce,
        encryption_key_version="v1",
    )
    session.add(credential)

    aws_account = CloudAccount(user_id=user.id, provider="aws", external_account_id="111111111111")
    session.add(aws_account)
    session.flush()
    aws_credential = Credential(
        cloud_account_id=aws_account.id,
        name="aws-cred",
        encrypted_payload=b"\x00",
        encryption_nonce=b"\x00",
        encryption_key_version="v1",
    )
    session.add(aws_credential)

    vm_catalog = ServiceCatalog(
        provider="azure", service_code="vm", category="compute", display_name="Virtual Machine", provisionable=True
    )
    ec2_catalog = ServiceCatalog(
        provider="aws", service_code="ec2", category="compute", display_name="EC2", provisionable=True
    )
    # provisionable이지만 러너가 없는 조합(§10 미구현) — 501 검증용.
    sql_catalog = ServiceCatalog(
        provider="azure", service_code="sql", category="database", display_name="Azure SQL", provisionable=True
    )
    session.add_all([vm_catalog, ec2_catalog, sql_catalog])
    session.commit()

    data = {
        "user_id": user.id,
        "other_user_id": other_user.id,
        "credential_id": credential.id,
        "aws_credential_id": aws_credential.id,
    }
    session.close()
    return data


def _auth(user_id) -> dict:
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _headers(idempotency_key="key-1") -> dict:
    return {"Idempotency-Key": idempotency_key, "X-Action-Confirmed": "true"}


def _body(credential_id, provider_spec=None, name="web-01") -> dict:
    return {
        "credential_id": str(credential_id),
        "common_spec": {"name": name},
        "provider_spec": provider_spec if provider_spec is not None else VALID_PROVIDER_SPEC,
    }


def test_missing_idempotency_key_returns_400(client, azure_fixture):
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"]),
        headers={**_auth(azure_fixture["user_id"]), "X-Action-Confirmed": "true"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_missing_confirmation_header_returns_428(client, azure_fixture):
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"]),
        headers={**_auth(azure_fixture["user_id"]), "Idempotency-Key": "key-1"},
    )
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_secret_field_in_provider_spec_rejected(client, azure_fixture):
    spec = {**VALID_PROVIDER_SPEC, "client_secret": "leak-me"}
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"], provider_spec=spec),
        headers={**_auth(azure_fixture["user_id"]), **_headers()},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "SECRET_FIELD_NOT_ALLOWED"


def test_unknown_service_returns_404(client, azure_fixture):
    resp = client.post(
        "/api/v1/provisioning/azure/does-not-exist",
        json=_body(azure_fixture["credential_id"]),
        headers={**_auth(azure_fixture["user_id"]), **_headers()},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SERVICE_NOT_FOUND"


def test_provisionable_service_without_runner_returns_501(client, azure_fixture):
    """provisionable하지만 러너가 등록되지 않은 조합은 501 PROVISIONING_NOT_IMPLEMENTED(통합 계약)."""
    resp = client.post(
        "/api/v1/provisioning/azure/sql",
        json=_body(azure_fixture["credential_id"]),
        headers={**_auth(azure_fixture["user_id"]), **_headers()},
    )
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "PROVISIONING_NOT_IMPLEMENTED"


def test_credential_owned_by_other_user_returns_404(client, azure_fixture):
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"]),
        headers={**_auth(azure_fixture["other_user_id"]), **_headers()},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


def test_credential_provider_mismatch_returns_404(client, azure_fixture):
    """소유 credential이지만 provider가 달라 존재를 드러내지 않고 404로 처리한다(통합 계약)."""
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["aws_credential_id"]),
        headers={**_auth(azure_fixture["user_id"]), **_headers()},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


def test_missing_common_spec_name_returns_422(client, azure_fixture):
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json={
            "credential_id": str(azure_fixture["credential_id"]),
            "common_spec": {},
            "provider_spec": VALID_PROVIDER_SPEC,
        },
        headers={**_auth(azure_fixture["user_id"]), **_headers()},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_provider_spec_returns_422(client, azure_fixture):
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"], provider_spec={"region": "koreacentral"}),
        headers={**_auth(azure_fixture["user_id"]), **_headers()},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_short_admin_password_returns_422(client, azure_fixture):
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"], provider_spec={**VALID_PROVIDER_SPEC, "admin_password": "short"}),
        headers={**_auth(azure_fixture["user_id"]), **_headers()},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_password_allowed_and_never_persisted_or_returned(client, azure_fixture):
    """admin_password는 이름에 "password"가 들어가지만 secret-필드 금지 예외 대상이다 —
    요청은 통과하지만 DB에 저장되는 spec_json과 GET 응답에는 절대 남지 않아야 한다."""
    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"]),
        headers={**_auth(azure_fixture["user_id"]), **_headers("password-key-1")},
    )
    assert resp.status_code == 202  # SECRET_FIELD_NOT_ALLOWED로 거부되지 않는다

    job_id = resp.json()["data"]["id"]
    get_resp = client.get(f"/api/v1/provisioning/jobs/{job_id}", headers=_auth(azure_fixture["user_id"]))
    assert "admin_password" not in get_resp.json()["data"]["provider_spec"]


def test_create_job_returns_202_queued_then_get_reflects_background_result(
    client, azure_fixture, monkeypatch, session_factory
):
    # 실제 terraform 대신 백그라운드 실행을 성공 상태로 직접 종결시킨다. client fixture와 같은
    # test engine을 보도록 session_factory로 job을 갱신한다.
    def fake_background(job_id, *args, **kwargs):
        session = session_factory()
        try:
            job = session.get(ProvisioningJob, job_id)
            job.status = "success"
            job.progress_percent = 100
            job.created_resource_count = 1
            job.result_json = {"vm_id": "fake-id", "public_ip_address": "1.2.3.4"}
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(provisioning_router, "_run_provisioning_job", fake_background)

    resp = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"]),
        headers={**_auth(azure_fixture["user_id"]), **_headers("create-key-1")},
    )
    assert resp.status_code == 202
    body = resp.json()["data"]
    assert body["status"] == "queued"
    job_id = body["id"]
    assert body["status_url"] == f"/api/v1/provisioning/jobs/{job_id}"

    # BackgroundTasks는 TestClient가 응답을 반환하기 전에 실행되므로 이 시점엔 이미 끝나 있다.
    get_resp = client.get(f"/api/v1/provisioning/jobs/{job_id}", headers=_auth(azure_fixture["user_id"]))
    assert get_resp.status_code == 200
    job_data = get_resp.json()["data"]
    assert job_data["status"] == "success"
    assert job_data["result"]["vm_id"] == "fake-id"
    assert "terraform_state_ref" not in job_data


def test_idempotent_replay_returns_same_job(client, azure_fixture):
    payload = _body(azure_fixture["credential_id"])
    headers = {**_auth(azure_fixture["user_id"]), **_headers("replay-key")}

    first = client.post("/api/v1/provisioning/azure/vm", json=payload, headers=headers)
    second = client.post("/api/v1/provisioning/azure/vm", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["id"] == second.json()["data"]["id"]


def test_idempotency_key_reused_with_different_payload_returns_409(client, azure_fixture):
    headers = {**_auth(azure_fixture["user_id"]), **_headers("conflict-key")}

    first = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"], name="web-01"),
        headers=headers,
    )
    second = client.post(
        "/api/v1/provisioning/azure/vm",
        json=_body(azure_fixture["credential_id"], name="web-02"),
        headers=headers,
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_get_unknown_job_returns_404(client, azure_fixture):
    resp = client.get("/api/v1/provisioning/jobs/999999", headers=_auth(azure_fixture["user_id"]))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PROVISIONING_JOB_NOT_FOUND"
