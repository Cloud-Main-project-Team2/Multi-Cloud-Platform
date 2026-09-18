"""GET /credentials/{id}/vm-sku-availability 검증(2026-09-18) — Azure VM SKU 사전 확인.

실제 CSP 호출은 하지 않는다 — `app.providers.azure.list_vm_sku_availability`를 monkeypatch한다
(`test_network_resources_api.py`와 동일 패턴).
"""

from __future__ import annotations

import app.routers.credentials as credentials_router
from app.models import CloudAccount, Credential
from app.resource_actions import ResourceActionError
from app.security.credential_crypto import encrypt_credential_json


def _make_account(db_session, user, provider, external_account_id="sub-1234"):
    row = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account, payload, verified=True):
    ciphertext, nonce = encrypt_credential_json(payload)
    row = Credential(
        cloud_account_id=account.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
        encryption_key_version="v1", verified=verified,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_returns_available_and_restricted_statuses(client, make_user, auth_header, db_session, monkeypatch):
    """회귀 시나리오 4: 구독에 restriction이 걸린 SKU는 available이 아니라 restricted로 와야 한다."""
    user = make_user()
    account = _make_account(db_session, user, "azure")
    cred = _make_credential(db_session, account, {"tenant_id": "t", "client_id": "c", "client_secret": "verylongsecretvalue"})
    db_session.commit()

    def _fake(secret_payload, subscription_id, region, sku_names):
        assert subscription_id == "sub-1234"
        assert region == "koreacentral"
        assert sku_names == ["B1s", "B2ats_v2"]
        return {
            "B1s": {"status": "restricted", "reason": "NotAvailableForSubscription"},
            "B2ats_v2": {"status": "available", "reason": None},
        }

    monkeypatch.setattr(credentials_router.azure_provider, "list_vm_sku_availability", _fake)

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/vm-sku-availability",
        params={"region": "koreacentral", "sku": ["B1s", "B2ats_v2"]},
        headers=auth_header(user),
    )
    assert resp.status_code == 200
    skus = {item["sku"]: item for item in resp.json()["data"]["skus"]}
    assert skus["B1s"]["status"] == "restricted"
    assert skus["B1s"]["reason"] == "NotAvailableForSubscription"
    assert skus["B2ats_v2"]["status"] == "available"
    assert skus["B2ats_v2"]["reason"] is None


def test_non_azure_provider_is_rejected(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", external_account_id="111122223333")
    cred = _make_credential(db_session, account, {"access_key_id": "AKIAFAKE", "secret_access_key": "shh"})
    db_session.commit()

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/vm-sku-availability",
        params={"region": "ap-northeast-2", "sku": ["t3.micro"]},
        headers=auth_header(user),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_OPERATION"


def test_rejects_unverified_credential(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "azure")
    cred = _make_credential(
        db_session, account, {"tenant_id": "t", "client_id": "c", "client_secret": "shh"}, verified=False
    )
    db_session.commit()

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/vm-sku-availability",
        params={"region": "koreacentral", "sku": ["B1s"]},
        headers=auth_header(user),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CLOUD_PERMISSION_DENIED"


def test_not_found_for_other_users_credential(client, make_user, auth_header, db_session):
    """회귀 시나리오 5: 다른 사용자의 credential로는 SKU를 조회할 수 없다(소유권 검사)."""
    owner = make_user(email="owner-sku@example.com")
    intruder = make_user(email="intruder-sku@example.com")
    account = _make_account(db_session, owner, "azure")
    cred = _make_credential(db_session, account, {"tenant_id": "t", "client_id": "c", "client_secret": "shh"})
    db_session.commit()

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/vm-sku-availability",
        params={"region": "koreacentral", "sku": ["B1s"]},
        headers=auth_header(intruder),
    )
    assert resp.status_code == 404


def test_query_failure_maps_to_502_not_available(client, make_user, auth_header, db_session, monkeypatch):
    """조회 자체가 실패하면(SDK 예외) 502로 응답해야 한다 — "사용 가능"으로 둔갑시키면 안 된다."""
    user = make_user()
    account = _make_account(db_session, user, "azure")
    cred = _make_credential(db_session, account, {"tenant_id": "t", "client_id": "c", "client_secret": "verylongsecretvalue"})
    db_session.commit()

    def _raise(secret_payload, subscription_id, region, sku_names):
        raise ResourceActionError("PROVIDER_API_ERROR")

    monkeypatch.setattr(credentials_router.azure_provider, "list_vm_sku_availability", _raise)

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/vm-sku-availability",
        params={"region": "koreacentral", "sku": ["B1s"]},
        headers=auth_header(user),
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PROVIDER_API_ERROR"
