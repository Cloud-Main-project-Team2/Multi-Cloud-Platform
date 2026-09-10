"""API 명세서 v1.1 §6 cloud-accounts/credentials 엔드포인트 검증.

실제 CSP 네트워크 호출은 하지 않는다 — `app.routers.credentials.verify_credential`을
monkeypatch해서 결정적으로 검증 결과를 제어한다(실제 SDK 연동은 수동으로 Swagger에서 확인).
"""

from __future__ import annotations

import json

import pytest

from app.models import CloudAccount, Credential, ProvisioningJob, ServiceCatalog
from app.providers import VerificationResult
import app.routers.credentials as credentials_router

AWS_SECRET = {"access_key_id": "AKIAFAKEKEY000001", "secret_access_key": "s3cr3t-should-never-leak"}
AZURE_SECRET = {
    "client_id": "11111111-2222-3333-4444-555555555555",
    "client_secret": "az-s3cr3t-should-never-leak",
    "tenant_id": "99999999-8888-7777-6666-555555555555",
}


def _mock_verify(monkeypatch, verified: bool, permission_scope: dict | None = None, error_code: str | None = None):
    def _fake(provider, external_account_id, secret_payload):
        return VerificationResult(
            verified=verified,
            permission_scope=permission_scope or {
                "inventory_read": verified, "resource_control": False, "provision": False, "cost_read": False,
            },
            error_code=error_code,
        )

    monkeypatch.setattr(credentials_router, "verify_credential", _fake)


def _create_credential(client, headers, provider="aws", **overrides):
    payload = {
        "external_account_id": "111122223333",
        "account_label": "테스트 계정",
        "name": "primary",
        "public_identifier": "AKIAFAKEKEY000001",
        "secret_payload": AWS_SECRET,
        "tags": {"environment": "test"},
        "display_order": 0,
    }
    payload.update(overrides)
    return client.post(f"/api/v1/credentials/{provider}", json=payload, headers=headers)


# --- POST /credentials/{provider} -----------------------------------------------------


def test_create_credential_verified_success(client, make_user, auth_header, monkeypatch):
    user = make_user()
    headers = auth_header(user)
    _mock_verify(monkeypatch, verified=True, permission_scope={
        "inventory_read": True, "resource_control": True, "provision": False, "cost_read": True,
    })

    resp = _create_credential(client, headers)

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["verified"] is True
    assert data["permission_scope"]["inventory_read"] is True
    assert data["masked_public_identifier"] == "AKIA••••••••0001"
    assert data["cloud_account_id"]
    for leaked_field in ("encrypted_payload", "encryption_nonce", "encryption_key_version", "public_identifier"):
        assert leaked_field not in data
    assert AWS_SECRET["secret_access_key"] not in json.dumps(resp.json())


def test_create_credential_verification_failure_is_still_saved(client, make_user, auth_header, monkeypatch, db_session):
    user = make_user()
    headers = auth_header(user)
    _mock_verify(monkeypatch, verified=False, error_code="PROVIDER_AUTHENTICATION_FAILED")

    resp = _create_credential(client, headers)

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["verified"] is False
    assert data["verified_at"] is None
    assert db_session.query(Credential).filter_by(id=int(data["id"])).one_or_none() is not None


def test_create_credential_reuses_existing_cloud_account(client, make_user, auth_header, monkeypatch, db_session):
    user = make_user()
    headers = auth_header(user)
    _mock_verify(monkeypatch, verified=True)

    first = _create_credential(client, headers, name="cred-a")
    second = _create_credential(client, headers, name="cred-b")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["cloud_account_id"] == second.json()["data"]["cloud_account_id"]
    assert db_session.query(CloudAccount).filter_by(user_id=user.id).count() == 1


def test_create_credential_duplicate_name_conflicts(client, make_user, auth_header, monkeypatch):
    user = make_user()
    headers = auth_header(user)
    _mock_verify(monkeypatch, verified=True)

    _create_credential(client, headers, name="dup")
    resp = _create_credential(client, headers, name="dup")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CREDENTIAL_ALREADY_EXISTS"


def test_create_credential_invalid_provider_rejected(client, make_user, auth_header):
    headers = auth_header(make_user())

    resp = _create_credential(client, headers, provider="ibm")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_credential_missing_secret_fields_rejected(client, make_user, auth_header):
    headers = auth_header(make_user())

    resp = _create_credential(client, headers, secret_payload={"access_key_id": "only-one-field"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_credential_azure_secret_shape(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)

    resp = _create_credential(
        client, headers, provider="azure",
        external_account_id="00000000-1111-2222-3333-444455556666",
        secret_payload=AZURE_SECRET,
    )

    assert resp.status_code == 201


# --- ownership across cloud-accounts/credentials ---------------------------------------


def test_other_users_cloud_account_is_not_found(client, make_user, auth_header, monkeypatch):
    owner = make_user(email="owner@example.com")
    intruder = make_user(email="intruder@example.com")
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers=auth_header(owner))
    cloud_account_id = created.json()["data"]["cloud_account_id"]

    resp = client.get(f"/api/v1/cloud-accounts/{cloud_account_id}", headers=auth_header(intruder))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CLOUD_ACCOUNT_NOT_FOUND"


def test_other_users_credential_is_not_found(client, make_user, auth_header, monkeypatch):
    owner = make_user(email="owner2@example.com")
    intruder = make_user(email="intruder2@example.com")
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers=auth_header(owner))
    credential_id = created.json()["data"]["id"]

    resp = client.post(f"/api/v1/credentials/{credential_id}/verify", headers=auth_header(intruder))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


def test_non_numeric_id_is_not_found(client, make_user, auth_header):
    resp = client.get("/api/v1/cloud-accounts/not-a-number", headers=auth_header(make_user()))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CLOUD_ACCOUNT_NOT_FOUND"


# --- cloud-accounts CRUD -----------------------------------------------------------------


def test_list_cloud_accounts_only_returns_own(client, make_user, auth_header, monkeypatch):
    owner = make_user(email="owner3@example.com")
    other = make_user(email="other3@example.com")
    _mock_verify(monkeypatch, verified=True)
    _create_credential(client, headers=auth_header(owner))
    _create_credential(client, headers=auth_header(other))

    resp = client.get("/api/v1/cloud-accounts", headers=auth_header(owner))

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1


def test_patch_cloud_account_label(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers)
    cloud_account_id = created.json()["data"]["cloud_account_id"]

    resp = client.patch(
        f"/api/v1/cloud-accounts/{cloud_account_id}",
        json={"account_label": "개발 AWS"},
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["account_label"] == "개발 AWS"


def test_delete_cloud_account_is_not_implemented(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers)
    cloud_account_id = created.json()["data"]["cloud_account_id"]

    resp = client.delete(f"/api/v1/cloud-accounts/{cloud_account_id}", headers=headers)

    assert resp.status_code == 501


def test_list_credentials_for_account_ordered(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)
    first = _create_credential(client, headers, name="second", display_order=1)
    _create_credential(client, headers, name="first", display_order=0)
    cloud_account_id = first.json()["data"]["cloud_account_id"]

    resp = client.get(f"/api/v1/cloud-accounts/{cloud_account_id}/credentials", headers=headers)

    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["data"]["items"]]
    assert names == ["first", "second"]


# --- PATCH /credentials/{id} -------------------------------------------------------------


def test_patch_credential_metadata_only_does_not_require_confirmation(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers)
    credential_id = created.json()["data"]["id"]

    resp = client.patch(
        f"/api/v1/credentials/{credential_id}",
        json={"tags": {"environment": "production"}, "display_order": 2},
        headers=headers,
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tags"] == {"environment": "production"}
    assert data["display_order"] == 2


def test_patch_credential_secret_replace_requires_confirmation(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers)
    credential_id = created.json()["data"]["id"]

    resp = client.patch(
        f"/api/v1/credentials/{credential_id}",
        json={"secret_payload": AWS_SECRET},
        headers=headers,
    )

    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_patch_credential_secret_replace_reverifies_with_confirmation(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True, permission_scope={
        "inventory_read": True, "resource_control": False, "provision": False, "cost_read": False,
    })
    created = _create_credential(client, headers)
    credential_id = created.json()["data"]["id"]

    _mock_verify(monkeypatch, verified=False, error_code="PROVIDER_AUTHENTICATION_FAILED")
    resp = client.patch(
        f"/api/v1/credentials/{credential_id}",
        json={"secret_payload": {"access_key_id": "AKIAROTATED0002", "secret_access_key": "rotated-secret"}},
        headers={**headers, "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["verified"] is False


# --- POST /credentials/{id}/verify --------------------------------------------------------


def test_verify_credential_updates_permission_scope(client, make_user, auth_header, monkeypatch, db_session):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=False)
    created = _create_credential(client, headers)
    credential_id = created.json()["data"]["id"]

    _mock_verify(monkeypatch, verified=True, permission_scope={
        "inventory_read": True, "resource_control": True, "provision": True, "cost_read": True,
    })
    resp = client.post(f"/api/v1/credentials/{credential_id}/verify", headers=headers)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["verified"] is True
    assert data["permission_scope"]["provision"] is True
    assert data["verified_at"] is not None


# --- DELETE /credentials/{id} --------------------------------------------------------------


def test_delete_credential_requires_confirmation(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers)
    credential_id = created.json()["data"]["id"]

    resp = client.delete(f"/api/v1/credentials/{credential_id}", headers=headers)

    assert resp.status_code == 428


def test_delete_credential_conflicts_when_job_is_active(client, make_user, auth_header, monkeypatch, db_session):
    user = make_user()
    headers = auth_header(user)
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers)
    credential_id = int(created.json()["data"]["id"])

    catalog = ServiceCatalog(provider="aws", service_code="ec2", category="compute", display_name="EC2")
    db_session.add(catalog)
    db_session.flush()
    db_session.add(
        ProvisioningJob(
            user_id=user.id, credential_id=credential_id, service_catalog_id=catalog.id,
            workspace_name="ws-1", idempotency_key="idem-1", spec_json={}, status="running",
        )
    )
    db_session.flush()

    resp = client.delete(
        f"/api/v1/credentials/{credential_id}", headers={**headers, "X-Action-Confirmed": "true"}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CREDENTIAL_IN_USE"


def test_delete_credential_succeeds(client, make_user, auth_header, monkeypatch, db_session):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)
    created = _create_credential(client, headers)
    credential_id = int(created.json()["data"]["id"])

    resp = client.delete(
        f"/api/v1/credentials/{credential_id}", headers={**headers, "X-Action-Confirmed": "true"}
    )

    assert resp.status_code == 204
    assert db_session.query(Credential).filter_by(id=credential_id).one_or_none() is None


# --- PUT /credentials/order ------------------------------------------------------------------


def test_reorder_credentials(client, make_user, auth_header, monkeypatch):
    headers = auth_header(make_user())
    _mock_verify(monkeypatch, verified=True)
    a = _create_credential(client, headers, name="a", display_order=0)
    b = _create_credential(client, headers, name="b", display_order=1)
    id_a, id_b = a.json()["data"]["id"], b.json()["data"]["id"]

    resp = client.put(
        "/api/v1/credentials/order",
        json={"items": [{"credential_id": id_a, "display_order": 5}, {"credential_id": id_b, "display_order": 1}]},
        headers=headers,
    )

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert [item["id"] for item in items] == [id_b, id_a]


def test_reorder_rejects_credential_owned_by_someone_else(client, make_user, auth_header, monkeypatch):
    owner = make_user(email="reorder-owner@example.com")
    other = make_user(email="reorder-other@example.com")
    _mock_verify(monkeypatch, verified=True)
    mine = _create_credential(client, headers=auth_header(owner))
    theirs = _create_credential(client, headers=auth_header(other))

    resp = client.put(
        "/api/v1/credentials/order",
        json={
            "items": [
                {"credential_id": mine.json()["data"]["id"], "display_order": 0},
                {"credential_id": theirs.json()["data"]["id"], "display_order": 1},
            ]
        },
        headers=auth_header(owner),
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"
