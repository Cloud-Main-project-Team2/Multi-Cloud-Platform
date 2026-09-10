"""POST /credentials/{provider}, POST /dev/users 통합 테스트.

test_provisioning_azure_vm.py와 같은 이유로 conftest.py의 db_session(롤백 전용)
대신 실제로 commit하는 세션을 쓴다.
"""

import base64
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import CloudAccount, Credential, User
from app.security import credential_crypto as cc

VALID_KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", VALID_KEY)
    cc.get_settings.cache_clear()
    yield
    cc.get_settings.cache_clear()


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
def user_id(session_factory):
    session = session_factory()
    user = User(email="a@example.com", normalized_email="a@example.com", name="A", affiliation_type="individual")
    session.add(user)
    session.commit()
    uid = user.id
    session.close()
    return uid


def _auth(uid) -> dict:
    return {"Authorization": f"Bearer {uid}"}


AZURE_SECRET = {
    "tenant_id": "t",
    "client_id": "c",
    "client_secret": "s3cr3t",
    "subscription_id": "sub-1",
}


def _body(**overrides) -> dict:
    body = {
        "external_account_id": "sub-1",
        "account_label": "테스트 계정",
        "name": "provisioner",
        "public_identifier": "c",
        "secret_payload": AZURE_SECRET,
        "tags": {},
        "display_order": 0,
    }
    body.update(overrides)
    return body


def test_create_credential_creates_account_and_encrypts_secret(client, user_id, session_factory):
    resp = client.post("/api/v1/credentials/azure", json=_body(), headers=_auth(user_id))
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["verified"] is False
    assert "secret_payload" not in data
    assert data["masked_public_identifier"].startswith("c")

    with session_factory() as session:
        account = session.get(CloudAccount, int(data["cloud_account_id"]))
        assert account.provider == "azure"
        assert account.external_account_id == "sub-1"

        credential = session.get(Credential, int(data["id"]))
        assert credential.encrypted_payload != b""
        decrypted = cc.decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
        assert decrypted == AZURE_SECRET


def test_second_credential_same_account_reuses_cloud_account(client, user_id, session_factory):
    first = client.post("/api/v1/credentials/azure", json=_body(name="cred-a"), headers=_auth(user_id))
    second = client.post("/api/v1/credentials/azure", json=_body(name="cred-b"), headers=_auth(user_id))

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["cloud_account_id"] == second.json()["data"]["cloud_account_id"]

    with session_factory() as session:
        assert session.query(CloudAccount).count() == 1
        assert session.query(Credential).count() == 2


def test_duplicate_credential_name_returns_409(client, user_id):
    first = client.post("/api/v1/credentials/azure", json=_body(), headers=_auth(user_id))
    second = client.post("/api/v1/credentials/azure", json=_body(), headers=_auth(user_id))

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CREDENTIAL_ALREADY_EXISTS"


def test_secret_field_in_tags_rejected(client, user_id):
    resp = client.post(
        "/api/v1/credentials/azure",
        json=_body(tags={"client_secret": "leak"}),
        headers=_auth(user_id),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "SECRET_FIELD_NOT_ALLOWED"


def test_unsupported_provider_returns_422(client, user_id):
    resp = client.post("/api/v1/credentials/oracle", json=_body(), headers=_auth(user_id))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_missing_authorization_returns_401(client):
    resp = client.post("/api/v1/credentials/azure", json=_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_dev_user_create_then_duplicate_email_conflicts(client):
    first = client.post("/api/v1/dev/users", json={"email": "dup@example.com", "name": "A"})
    second = client.post("/api/v1/dev/users", json={"email": "dup@example.com", "name": "B"})

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"
