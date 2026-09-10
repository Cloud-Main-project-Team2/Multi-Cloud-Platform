"""API 명세서 v1.1 §8 인벤토리 API 검증. POST /resources/action의 실제 CSP 호출은 하지 않는다 —
`app.routers.resources.perform_action`을 monkeypatch해서 결정적으로 제어한다.
"""

from __future__ import annotations

import datetime as dt

from app.models import AuditEvent, CloudAccount, Credential, Resource, ServiceCatalog
from app.resource_actions import ResourceActionError
from app.security.credential_crypto import encrypt_credential_json

_NOW = dt.datetime.now(dt.timezone.utc)


def _make_service(db_session, provider, service_code, category="compute", display_name=None):
    row = db_session.query(ServiceCatalog).filter_by(provider=provider, service_code=service_code).one_or_none()
    if row is not None:
        return row
    row = ServiceCatalog(provider=provider, service_code=service_code, category=category, display_name=display_name or service_code)
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user, provider, external_account_id, label=None):
    row = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id, account_label=label)
    db_session.add(row)
    db_session.flush()
    return row


def _make_resource(db_session, account, service, external_resource_id, **overrides):
    defaults = dict(
        cloud_account_id=account.id,
        service_catalog_id=service.id,
        provider_resource_key=f"{account.provider}:{external_resource_id}",
        external_resource_id=external_resource_id,
        original_resource_type=overrides.pop("original_resource_type", "Instance"),
        name=overrides.pop("name", external_resource_id),
        region=overrides.pop("region", "us-east-1"),
        status=overrides.pop("status", "RUNNING"),
        first_seen_at=_NOW,
        last_seen_at=_NOW,
        last_synced_at=_NOW,
    )
    defaults.update(overrides)
    row = Resource(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account, name="cred", verified=True, permission_scope=None):
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    row = Credential(
        cloud_account_id=account.id,
        name=name,
        encrypted_payload=ciphertext,
        encryption_nonce=nonce,
        encryption_key_version="v1",
        verified=verified,
        permission_scope=permission_scope or {},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _setup_aws_ec2(db_session, user, *, status="RUNNING", verified=True, permission_scope=None):
    account = _make_account(db_session, user, "aws", "111122223333", "prod-aws")
    service = _make_service(db_session, "aws", "ec2", "compute", "EC2")
    credential = _make_credential(db_session, account, verified=verified, permission_scope=permission_scope)
    resource = _make_resource(
        db_session, account, service, "i-0abc123", original_resource_type="EC2 Instance",
        name="web-01", region="ap-northeast-2", status=status,
    )
    return account, service, credential, resource


# --- GET /resources ----------------------------------------------------------------------


def test_list_resources_only_returns_owner_resources(client, make_user, auth_header, db_session):
    owner = make_user(email="owner@example.com")
    other = make_user(email="other@example.com")
    _setup_aws_ec2(db_session, owner)
    _setup_aws_ec2(db_session, other)
    db_session.commit()

    resp = client.get("/api/v1/resources", headers=auth_header(owner))

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["cloud_account"]["provider"] == "aws"
    assert "provider_resource_key" not in items[0]


def test_list_resources_filters_by_provider(client, make_user, auth_header, db_session):
    user = make_user()
    aws_account = _make_account(db_session, user, "aws", "111122223333")
    aws_service = _make_service(db_session, "aws", "ec2")
    _make_resource(db_session, aws_account, aws_service, "i-aws-1")

    gcp_account = _make_account(db_session, user, "gcp", "proj-1")
    gcp_service = _make_service(db_session, "gcp", "compute_engine")
    _make_resource(db_session, gcp_account, gcp_service, "gcp-vm-1")
    db_session.commit()

    resp = client.get("/api/v1/resources", params={"provider": "gcp"}, headers=auth_header(user))

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["external_resource_id"] == "gcp-vm-1"


def test_list_resources_search_by_resource_field(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    service = _make_service(db_session, "aws", "ec2")
    _make_resource(db_session, account, service, "mcp-a1b2-vm", original_resource_type="EC2 Instance")
    _make_resource(db_session, account, service, "mcp-a1b2-disk", original_resource_type="EBS Volume")
    db_session.commit()

    resp = client.get(
        "/api/v1/resources", params={"q": "disk", "search_field": "resource"}, headers=auth_header(user)
    )

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["external_resource_id"] == "mcp-a1b2-disk"


def test_list_resources_excludes_stale_and_deleted_by_default(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    service = _make_service(db_session, "aws", "ec2")
    _make_resource(db_session, account, service, "fresh")
    _make_resource(db_session, account, service, "stale-one", is_stale=True)
    _make_resource(db_session, account, service, "deleted-one", deleted_at=_NOW)
    db_session.commit()

    resp = client.get("/api/v1/resources", headers=auth_header(user))
    assert [item["external_resource_id"] for item in resp.json()["data"]["items"]] == ["fresh"]

    resp_all = client.get(
        "/api/v1/resources", params={"include_stale": "true", "include_deleted": "true"}, headers=auth_header(user)
    )
    assert len(resp_all.json()["data"]["items"]) == 3


def test_cost_summary_present_when_cost_data_exists(client, make_user, auth_header, db_session):
    from decimal import Decimal

    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    service = _make_service(db_session, "aws", "ec2")
    _make_resource(
        db_session, account, service, "i-cost-1",
        estimated_monthly_cost=Decimal("481.50"), collected_cost_amount=Decimal("128.40"),
        cost_currency="USD", cost_source="seed",
    )
    db_session.commit()

    resp = client.get("/api/v1/resources", headers=auth_header(user))
    cost = resp.json()["data"]["items"][0]["cost_summary"]
    assert cost["estimated_monthly_cost"] == "481.500000"
    assert cost["currency"] == "USD"


# --- GET /resources/summary ---------------------------------------------------------------


def test_resources_summary_counts(client, make_user, auth_header, db_session):
    user = make_user()
    aws_account = _make_account(db_session, user, "aws", "111122223333")
    aws_service = _make_service(db_session, "aws", "ec2")
    _make_resource(db_session, aws_account, aws_service, "a1")
    _make_resource(db_session, aws_account, aws_service, "a2", is_stale=True)

    azure_account = _make_account(db_session, user, "azure", "sub-1")
    azure_service = _make_service(db_session, "azure", "vm")
    _make_resource(db_session, azure_account, azure_service, "az1")
    db_session.commit()

    resp = client.get(
        "/api/v1/resources/summary", params={"include_stale": "true"}, headers=auth_header(user)
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_resources"] == 3
    assert data["active_resources"] == 2
    assert data["stale_resources"] == 1
    assert {"provider": "aws", "count": 2} in data["by_provider"]
    assert {"provider": "azure", "count": 1} in data["by_provider"]
    assert data["last_synced_at"] is not None


# --- GET /resources/{id} ------------------------------------------------------------------


def test_get_resource_detail(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.get(f"/api/v1/resources/{resource.id}", headers=auth_header(user))

    assert resp.status_code == 200
    assert resp.json()["data"]["external_resource_id"] == "i-0abc123"


def test_get_resource_detail_not_found_for_other_user(client, make_user, auth_header, db_session):
    owner = make_user(email="owner2@example.com")
    intruder = make_user(email="intruder2@example.com")
    _, _, _, resource = _setup_aws_ec2(db_session, owner)
    db_session.commit()

    resp = client.get(f"/api/v1/resources/{resource.id}", headers=auth_header(intruder))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


# --- POST /resources/action ---------------------------------------------------------------


def test_action_requires_confirmation(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "stop", "resource_ids": [str(resource.id)]},
        headers=auth_header(user),
    )

    assert resp.status_code == 428


def test_action_rejects_duplicate_resource_ids(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "stop", "resource_ids": [str(resource.id), str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 422


def test_action_unsupported_operation_for_ebs_start(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    service = _make_service(db_session, "aws", "ec2")
    _make_credential(db_session, account, verified=True)
    volume = _make_resource(db_session, account, service, "vol-1", original_resource_type="EBS Volume", status="AVAILABLE")
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "start", "resource_ids": [str(volume.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 200
    result = resp.json()["data"]["results"][0]
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "UNSUPPORTED_OPERATION"


def test_action_rejects_when_no_verified_credential(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, credential, resource = _setup_aws_ec2(db_session, user, verified=False)
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "stop", "resource_ids": [str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    result = resp.json()["data"]["results"][0]
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "CLOUD_PERMISSION_DENIED"


def test_action_rejects_when_permission_scope_denies_resource_control(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(
        db_session, user, permission_scope={"inventory_read": True, "resource_control": False, "provision": False, "cost_read": False}
    )
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "stop", "resource_ids": [str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    result = resp.json()["data"]["results"][0]
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "CLOUD_PERMISSION_DENIED"


def test_action_allows_when_permission_scope_is_empty_like_seed_data(client, make_user, auth_header, db_session, monkeypatch):
    import app.routers.resources as resources_router

    monkeypatch.setattr(resources_router, "perform_action", lambda **kwargs: None)
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user, status="STOPPED", permission_scope={})
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "start", "resource_ids": [str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    result = resp.json()["data"]["results"][0]
    assert result["status"] == "success"


def test_action_already_deleted_is_rejected(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    resource.deleted_at = _NOW
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "stop", "resource_ids": [str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    result = resp.json()["data"]["results"][0]
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "RESOURCE_ALREADY_DELETED"


def test_action_stale_is_rejected(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    resource.is_stale = True
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "stop", "resource_ids": [str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    result = resp.json()["data"]["results"][0]
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "RESOURCE_STALE"


def test_action_success_updates_status_and_audit(client, make_user, auth_header, db_session, monkeypatch):
    import app.routers.resources as resources_router

    calls = []
    monkeypatch.setattr(resources_router, "perform_action", lambda **kwargs: calls.append(kwargs))

    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user, status="STOPPED")
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "start", "resource_ids": [str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 200
    result = resp.json()["data"]["results"][0]
    assert result["status"] == "success"
    assert len(calls) == 1

    db_session.refresh(resource)
    assert resource.status == "RUNNING"

    audit = db_session.query(AuditEvent).filter_by(target_type="resource", target_id=str(resource.id)).one()
    assert audit.action == "resource.start"
    assert audit.result == "success"


def test_action_delete_sets_deleted_at(client, make_user, auth_header, db_session, monkeypatch):
    import app.routers.resources as resources_router

    monkeypatch.setattr(resources_router, "perform_action", lambda **kwargs: None)
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "delete", "resource_ids": [str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 200
    db_session.refresh(resource)
    assert resource.deleted_at is not None
    assert resource.status == "DELETED"


def test_action_provider_error_maps_to_failed(client, make_user, auth_header, db_session, monkeypatch):
    import app.routers.resources as resources_router

    def _raise(**kwargs):
        raise ResourceActionError("PROVIDER_API_ERROR")

    monkeypatch.setattr(resources_router, "perform_action", _raise)
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "stop", "resource_ids": [str(resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    result = resp.json()["data"]["results"][0]
    assert result["status"] == "failed"
    assert result["error"]["code"] == "PROVIDER_API_ERROR"

    audit = db_session.query(AuditEvent).filter_by(target_type="resource", target_id=str(resource.id)).one()
    assert audit.result == "failure"


def test_action_partial_success_across_multiple_resources(client, make_user, auth_header, db_session, monkeypatch):
    import app.routers.resources as resources_router

    monkeypatch.setattr(resources_router, "perform_action", lambda **kwargs: None)
    user = make_user()
    account, service, _, ok_resource = _setup_aws_ec2(db_session, user, status="STOPPED")
    unsupported_resource = _make_resource(
        db_session, account, service, "vol-2", original_resource_type="EBS Volume", status="AVAILABLE"
    )
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "start", "resource_ids": [str(ok_resource.id), str(unsupported_resource.id)]},
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 200
    results = {r["resource_id"]: r for r in resp.json()["data"]["results"]}
    assert results[str(ok_resource.id)]["status"] == "success"
    assert results[str(unsupported_resource.id)]["status"] == "rejected"


def test_action_foreign_resource_id_is_not_found(client, make_user, auth_header, db_session):
    owner = make_user(email="owner3@example.com")
    intruder = make_user(email="intruder3@example.com")
    _, _, _, resource = _setup_aws_ec2(db_session, owner)
    db_session.commit()

    resp = client.post(
        "/api/v1/resources/action",
        json={"action": "stop", "resource_ids": [str(resource.id)]},
        headers={**auth_header(intruder), "X-Action-Confirmed": "true"},
    )

    result = resp.json()["data"]["results"][0]
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "RESOURCE_NOT_FOUND"
