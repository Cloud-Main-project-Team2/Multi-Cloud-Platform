"""GET /credentials/{id}/network-resources 검증(2026-09-17) — 프로비저닝 폼 "기존 리소스 사용"이
실제 VPC/서브넷/보안그룹(Azure는 리소스그룹/VNet/NSG, GCP는 네트워크) 목록을 가져오는 API.

실제 CSP 네트워크 호출은 하지 않는다 — provider 모듈의 `list_network_resources`를 monkeypatch한다.
"""

from __future__ import annotations

import app.routers.credentials as credentials_router
from app.models import CloudAccount, Credential
from app.resource_actions import ResourceActionError
from app.security.credential_crypto import encrypt_credential_json


def _make_account(db_session, user, provider, external_account_id="111122223333"):
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


def test_aws_requires_region(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws")
    cred = _make_credential(db_session, account, {"access_key_id": "AKIAFAKE", "secret_access_key": "shh"})
    db_session.commit()

    resp = client.get(f"/api/v1/credentials/{cred.id}/network-resources", headers=auth_header(user))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_aws_returns_vpcs_subnets_security_groups(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    account = _make_account(db_session, user, "aws")
    cred = _make_credential(db_session, account, {"access_key_id": "AKIAFAKE", "secret_access_key": "shh"})
    db_session.commit()

    def _fake(secret_payload, region):
        assert region == "ap-northeast-2"
        return {
            "vpcs": [{"id": "vpc-1", "cidr_block": "10.0.0.0/16", "name": "main", "is_default": True}],
            "subnets": [{"id": "subnet-1", "vpc_id": "vpc-1", "availability_zone": "ap-northeast-2a", "cidr_block": "10.0.1.0/24", "name": None}],
            "security_groups": [{"id": "sg-1", "vpc_id": "vpc-1", "name": "default"}],
        }

    monkeypatch.setattr(credentials_router.aws_provider, "list_network_resources", _fake)

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/network-resources",
        params={"region": "ap-northeast-2"},
        headers=auth_header(user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["vpcs"][0]["id"] == "vpc-1"
    assert data["subnets"][0]["vpc_id"] == "vpc-1"
    assert data["security_groups"][0]["id"] == "sg-1"
    assert data["resource_groups"] == []
    assert data["networks"] == []


def test_azure_does_not_require_region(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    account = _make_account(db_session, user, "azure")
    cred = _make_credential(
        db_session, account,
        {"tenant_id": "t", "client_id": "c", "client_secret": "verylongsecretvalue"},
    )
    db_session.commit()

    def _fake(secret_payload, subscription_id):
        assert subscription_id == "111122223333"
        return {
            "resource_groups": [{"name": "rg-1", "location": "koreacentral"}],
            "virtual_networks": [{"id": "/subscriptions/x/resourceGroups/rg-1/providers/Microsoft.Network/virtualNetworks/vnet1", "name": "vnet1", "resource_group": "rg-1", "location": "koreacentral", "address_space": ["10.0.0.0/16"]}],
            "network_security_groups": [{"id": "/subscriptions/x/resourceGroups/rg-1/providers/Microsoft.Network/networkSecurityGroups/nsg1", "name": "nsg1", "resource_group": "rg-1", "location": "koreacentral"}],
            "azure_subnets": [{"id": "/subscriptions/x/resourceGroups/rg-1/providers/Microsoft.Network/virtualNetworks/vnet1/subnets/subnet1", "name": "subnet1", "vnet_name": "vnet1", "resource_group": "rg-1", "address_prefix": "10.0.1.0/24"}],
        }

    monkeypatch.setattr(credentials_router.azure_provider, "list_network_resources", _fake)

    resp = client.get(f"/api/v1/credentials/{cred.id}/network-resources", headers=auth_header(user))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["resource_groups"][0]["name"] == "rg-1"
    assert data["virtual_networks"][0]["name"] == "vnet1"
    assert data["network_security_groups"][0]["name"] == "nsg1"
    assert data["azure_subnets"][0]["name"] == "subnet1"


def test_gcp_returns_networks(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    account = _make_account(db_session, user, "gcp", external_account_id="proj-1")
    cred = _make_credential(
        db_session, account,
        {"type": "service_account", "client_email": "x@proj-1.iam.gserviceaccount.com",
         "private_key_id": "k", "private_key": "pk", "token_uri": "https://oauth2.googleapis.com/token"},
    )
    db_session.commit()

    def _fake(secret_payload, project_id):
        assert project_id == "proj-1"
        return {"networks": [{"name": "default", "self_link": "https://.../networks/default", "auto_create_subnetworks": True}]}

    monkeypatch.setattr(credentials_router.gcp_provider, "list_network_resources", _fake)

    resp = client.get(f"/api/v1/credentials/{cred.id}/network-resources", headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["networks"][0]["name"] == "default"


def test_rejects_unverified_credential(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "azure")
    cred = _make_credential(
        db_session, account, {"tenant_id": "t", "client_id": "c", "client_secret": "shh"}, verified=False
    )
    db_session.commit()

    resp = client.get(f"/api/v1/credentials/{cred.id}/network-resources", headers=auth_header(user))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CLOUD_PERMISSION_DENIED"


def test_not_found_for_other_users_credential(client, make_user, auth_header, db_session):
    owner = make_user(email="owner-net@example.com")
    intruder = make_user(email="intruder-net@example.com")
    account = _make_account(db_session, owner, "azure")
    cred = _make_credential(db_session, account, {"tenant_id": "t", "client_id": "c", "client_secret": "shh"})
    db_session.commit()

    resp = client.get(f"/api/v1/credentials/{cred.id}/network-resources", headers=auth_header(intruder))
    assert resp.status_code == 404


def test_provider_error_maps_to_502(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    account = _make_account(db_session, user, "aws")
    cred = _make_credential(db_session, account, {"access_key_id": "AKIAFAKE", "secret_access_key": "shh"})
    db_session.commit()

    def _raise(secret_payload, region):
        raise ResourceActionError("PROVIDER_API_ERROR")

    monkeypatch.setattr(credentials_router.aws_provider, "list_network_resources", _raise)

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/network-resources",
        params={"region": "ap-northeast-2"},
        headers=auth_header(user),
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PROVIDER_API_ERROR"
