"""보안그룹 관리(2026-09-17) — AWS SG / Azure NSG / GCP 방화벽 규칙 CRUD 검증.

실제 CSP 호출은 하지 않는다 — provider 모듈 함수를 monkeypatch한다(`test_network_resources_api.py`와
동일 스타일).
"""

from __future__ import annotations

import app.routers.security_groups as security_groups_router
from app.models import CloudAccount, Credential
from app.resource_actions import ResourceActionError
from app.security.credential_crypto import encrypt_credential_json

CONFIRMED = {"X-Action-Confirmed": "true"}


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


def _aws_cred(db_session, user):
    account = _make_account(db_session, user, "aws")
    cred = _make_credential(db_session, account, {"access_key_id": "AKIAFAKE", "secret_access_key": "shh"})
    db_session.commit()
    return cred


def _azure_cred(db_session, user):
    account = _make_account(db_session, user, "azure")
    cred = _make_credential(
        db_session, account, {"tenant_id": "t", "client_id": "c", "client_secret": "verylongsecretvalue"}
    )
    db_session.commit()
    return cred


def _gcp_cred(db_session, user):
    account = _make_account(db_session, user, "gcp", external_account_id="proj-1")
    cred = _make_credential(
        db_session, account,
        {"type": "service_account", "client_email": "x@proj-1.iam.gserviceaccount.com",
         "private_key_id": "k", "private_key": "pk", "token_uri": "https://oauth2.googleapis.com/token"},
    )
    db_session.commit()
    return cred


# --- 목록 조회 -------------------------------------------------------------------------


def test_aws_requires_region(client, make_user, auth_header, db_session):
    user = make_user()
    cred = _aws_cred(db_session, user)

    resp = client.get(f"/api/v1/credentials/{cred.id}/security-groups", headers=auth_header(user))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_aws_lists_groups_with_rules(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _aws_cred(db_session, user)

    def _fake(secret_payload, region):
        assert region == "ap-northeast-2"
        return [
            {
                "id": "sg-1", "name": "web", "description": "web sg", "vpc_id": "vpc-1",
                "ingress_rules": [
                    {"rule_id": "sgr-1", "direction": "ingress", "protocol": "tcp", "from_port": 80,
                     "to_port": 80, "cidr": "0.0.0.0/0", "description": None}
                ],
                "egress_rules": [],
            }
        ]

    monkeypatch.setattr(security_groups_router.aws_provider, "list_security_groups", _fake)

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/security-groups",
        params={"region": "ap-northeast-2"},
        headers=auth_header(user),
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert items[0]["id"] == "sg-1"
    assert items[0]["ingress_rules"][0]["rule_id"] == "sgr-1"


def test_azure_lists_groups(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _azure_cred(db_session, user)

    def _fake(secret_payload, subscription_id):
        assert subscription_id == "111122223333"
        return [
            {
                "id": "/subscriptions/x/resourceGroups/rg-1/providers/Microsoft.Network/networkSecurityGroups/nsg1",
                "name": "nsg1", "resource_group": "rg-1", "location": "koreacentral",
                "rules": [{"name": "allow-web", "priority": 100, "direction": "Inbound", "access": "Allow",
                           "protocol": "Tcp", "source_address_prefix": "*", "destination_port_range": "80",
                           "description": None}],
            }
        ]

    monkeypatch.setattr(security_groups_router.azure_provider, "list_security_groups", _fake)

    resp = client.get(f"/api/v1/credentials/{cred.id}/security-groups", headers=auth_header(user))
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert items[0]["name"] == "nsg1"
    assert items[0]["rules"][0]["name"] == "allow-web"


def test_gcp_lists_firewall_rules_flat(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _gcp_cred(db_session, user)

    def _fake(secret_payload, project_id):
        assert project_id == "proj-1"
        return [
            {"name": "allow-ssh", "network": "default", "direction": "INGRESS", "priority": 1000,
             "action": "allow", "protocol": "tcp", "ports": ["22"], "source_ranges": ["0.0.0.0/0"],
             "target_tags": []}
        ]

    monkeypatch.setattr(security_groups_router.gcp_provider, "list_firewall_rules", _fake)

    resp = client.get(f"/api/v1/credentials/{cred.id}/security-groups", headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["items"][0]["name"] == "allow-ssh"


def test_rejects_unverified_credential(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "azure")
    cred = _make_credential(
        db_session, account, {"tenant_id": "t", "client_id": "c", "client_secret": "shh"}, verified=False
    )
    db_session.commit()

    resp = client.get(f"/api/v1/credentials/{cred.id}/security-groups", headers=auth_header(user))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CLOUD_PERMISSION_DENIED"


def test_not_found_for_other_users_credential(client, make_user, auth_header, db_session):
    owner = make_user(email="owner-sg@example.com")
    intruder = make_user(email="intruder-sg@example.com")
    cred = _azure_cred(db_session, owner)

    resp = client.get(f"/api/v1/credentials/{cred.id}/security-groups", headers=auth_header(intruder))
    assert resp.status_code == 404


def test_provider_error_maps_to_502(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _aws_cred(db_session, user)

    def _raise(secret_payload, region):
        raise ResourceActionError("PROVIDER_API_ERROR")

    monkeypatch.setattr(security_groups_router.aws_provider, "list_security_groups", _raise)

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/security-groups",
        params={"region": "ap-northeast-2"},
        headers=auth_header(user),
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PROVIDER_API_ERROR"


def test_access_denied_cause_surfaces_specific_reason(client, make_user, auth_header, db_session, monkeypatch):
    """provider가 원문 SDK 예외를 `from`으로 감싸기만 하고 message를 안 채워도(실제 provider
    함수들의 관례), `_error_message()`가 `__cause__`에서 원문을 뽑아 응답 message에 싣고
    `app.main._error_body`가 거기서 "권한이 부족합니다..." 구체 원인을 자동으로 뽑아야 한다
    (2026-09-17 — 프로비저닝뿐 아니라 보안그룹 관리도 같은 안내를 받게 하기 위한 변경)."""
    user = make_user()
    cred = _aws_cred(db_session, user)

    def _raise(secret_payload, region):
        try:
            raise Exception(
                "An error occurred (UnauthorizedOperation) when calling the DescribeSecurityGroups "
                "operation: You are not authorized to perform this operation."
            )
        except Exception as exc:
            raise ResourceActionError("PROVIDER_API_ERROR") from exc

    monkeypatch.setattr(security_groups_router.aws_provider, "list_security_groups", _raise)

    resp = client.get(
        f"/api/v1/credentials/{cred.id}/security-groups",
        params={"region": "ap-northeast-2"},
        headers=auth_header(user),
    )
    assert resp.status_code == 502
    body = resp.json()["error"]
    assert body["code"] == "PROVIDER_API_ERROR"
    assert "UnauthorizedOperation" in body["message"]
    assert body.get("specific_reason") is not None
    assert "권한" in body["specific_reason"]


# --- 생성 -----------------------------------------------------------------------------


def test_create_requires_confirmation_header(client, make_user, auth_header, db_session):
    user = make_user()
    cred = _aws_cred(db_session, user)

    resp = client.post(
        f"/api/v1/credentials/{cred.id}/security-groups",
        params={"region": "ap-northeast-2"},
        json={"aws": {"name": "web", "description": "web sg", "vpc_id": "vpc-1"}},
        headers=auth_header(user),
    )
    assert resp.status_code == 428


def test_aws_create_security_group(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _aws_cred(db_session, user)

    def _fake(secret_payload, region, name, description, vpc_id):
        assert (region, name, description, vpc_id) == ("ap-northeast-2", "web", "web sg", "vpc-1")
        return {"id": "sg-new", "name": name, "description": description, "vpc_id": vpc_id,
                "ingress_rules": [], "egress_rules": []}

    monkeypatch.setattr(security_groups_router.aws_provider, "create_security_group", _fake)

    resp = client.post(
        f"/api/v1/credentials/{cred.id}/security-groups",
        params={"region": "ap-northeast-2"},
        json={"aws": {"name": "web", "description": "web sg", "vpc_id": "vpc-1"}},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["aws"]["id"] == "sg-new"


def test_aws_create_missing_provider_field_is_422(client, make_user, auth_header, db_session):
    user = make_user()
    cred = _aws_cred(db_session, user)

    resp = client.post(
        f"/api/v1/credentials/{cred.id}/security-groups",
        params={"region": "ap-northeast-2"},
        json={"azure": {"name": "x", "resource_group": "rg", "location": "koreacentral"}},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 422


def test_gcp_create_firewall_rule(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _gcp_cred(db_session, user)

    def _fake(secret_payload, project_id, rule):
        assert project_id == "proj-1"
        assert rule["name"] == "allow-ssh"
        return {"name": "allow-ssh", "network": "default", "direction": "INGRESS", "priority": 1000,
                "action": "allow", "protocol": "tcp", "ports": ["22"], "source_ranges": ["0.0.0.0/0"],
                "target_tags": []}

    monkeypatch.setattr(security_groups_router.gcp_provider, "create_firewall_rule", _fake)

    resp = client.post(
        f"/api/v1/credentials/{cred.id}/security-groups",
        json={"gcp": {"name": "allow-ssh", "network": "default", "protocol": "tcp",
                      "ports": ["22"], "source_ranges": ["0.0.0.0/0"]}},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["gcp"]["name"] == "allow-ssh"


# --- 삭제 -----------------------------------------------------------------------------


def test_aws_delete_security_group(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _aws_cred(db_session, user)
    calls = []
    monkeypatch.setattr(
        security_groups_router.aws_provider, "delete_security_group",
        lambda secret_payload, region, group_id: calls.append((region, group_id)),
    )

    resp = client.delete(
        f"/api/v1/credentials/{cred.id}/security-groups/sg-1",
        params={"region": "ap-northeast-2"},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 204
    assert calls == [("ap-northeast-2", "sg-1")]


def test_azure_delete_requires_resource_group(client, make_user, auth_header, db_session):
    user = make_user()
    cred = _azure_cred(db_session, user)

    resp = client.delete(
        f"/api/v1/credentials/{cred.id}/security-groups/nsg1",
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_azure_delete_security_group(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _azure_cred(db_session, user)
    calls = []
    monkeypatch.setattr(
        security_groups_router.azure_provider, "delete_security_group",
        lambda secret_payload, subscription_id, resource_group, name: calls.append((resource_group, name)),
    )

    resp = client.delete(
        f"/api/v1/credentials/{cred.id}/security-groups/nsg1",
        params={"resource_group": "rg-1"},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 204
    assert calls == [("rg-1", "nsg1")]


def test_gcp_delete_is_firewall_rule_delete(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _gcp_cred(db_session, user)
    calls = []
    monkeypatch.setattr(
        security_groups_router.gcp_provider, "delete_firewall_rule",
        lambda secret_payload, project_id, name: calls.append((project_id, name)),
    )

    resp = client.delete(
        f"/api/v1/credentials/{cred.id}/security-groups/allow-ssh",
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 204
    assert calls == [("proj-1", "allow-ssh")]


# --- 규칙 추가/삭제 (AWS/Azure 전용, GCP는 거부) ------------------------------------------


def test_aws_add_rule(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _aws_cred(db_session, user)

    def _fake(secret_payload, region, group_id, direction, protocol, from_port, to_port, cidr, description):
        assert (group_id, direction, protocol, from_port, to_port, cidr) == (
            "sg-1", "ingress", "tcp", 80, 80, "0.0.0.0/0"
        )
        return {"rule_id": "sgr-new", "direction": direction, "protocol": protocol,
                "from_port": from_port, "to_port": to_port, "cidr": cidr, "description": description}

    monkeypatch.setattr(security_groups_router.aws_provider, "add_security_group_rule", _fake)

    resp = client.post(
        f"/api/v1/credentials/{cred.id}/security-groups/sg-1/rules",
        params={"region": "ap-northeast-2"},
        json={"aws": {"direction": "ingress", "protocol": "tcp", "from_port": 80, "to_port": 80,
                      "cidr": "0.0.0.0/0"}},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["aws"]["rule_id"] == "sgr-new"


def test_aws_remove_rule_requires_direction(client, make_user, auth_header, db_session):
    user = make_user()
    cred = _aws_cred(db_session, user)

    resp = client.delete(
        f"/api/v1/credentials/{cred.id}/security-groups/sg-1/rules/sgr-1",
        params={"region": "ap-northeast-2"},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 422


def test_aws_remove_rule(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    cred = _aws_cred(db_session, user)
    calls = []
    monkeypatch.setattr(
        security_groups_router.aws_provider, "remove_security_group_rule",
        lambda secret_payload, region, group_id, direction, rule_id: calls.append((group_id, direction, rule_id)),
    )

    resp = client.delete(
        f"/api/v1/credentials/{cred.id}/security-groups/sg-1/rules/sgr-1",
        params={"region": "ap-northeast-2", "direction": "ingress"},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 204
    assert calls == [("sg-1", "ingress", "sgr-1")]


def test_gcp_add_rule_is_rejected(client, make_user, auth_header, db_session):
    user = make_user()
    cred = _gcp_cred(db_session, user)

    resp = client.post(
        f"/api/v1/credentials/{cred.id}/security-groups/allow-ssh/rules",
        json={"aws": {"direction": "ingress", "protocol": "tcp", "cidr": "0.0.0.0/0"}},
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_gcp_remove_rule_is_rejected(client, make_user, auth_header, db_session):
    user = make_user()
    cred = _gcp_cred(db_session, user)

    resp = client.delete(
        f"/api/v1/credentials/{cred.id}/security-groups/allow-ssh/rules/whatever",
        headers={**auth_header(user), **CONFIRMED},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
