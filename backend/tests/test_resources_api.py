"""API 명세서 v1.1 §8 인벤토리 조회 API(GET /resources, /resources/summary, /resources/{id}) 검증."""

from __future__ import annotations

import datetime as dt

from app.models import CloudAccount, ServiceCatalog, Resource

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


def _setup_aws_ec2(db_session, user, *, status="RUNNING"):
    account = _make_account(db_session, user, "aws", "111122223333", "prod-aws")
    service = _make_service(db_session, "aws", "ec2", "compute", "EC2")
    resource = _make_resource(
        db_session, account, service, "i-0abc123", original_resource_type="EC2 Instance",
        name="web-01", region="ap-northeast-2", status=status,
    )
    return account, service, resource


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
    _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.get(f"/api/v1/resources/{resource.id}", headers=auth_header(user))

    assert resp.status_code == 200
    assert resp.json()["data"]["external_resource_id"] == "i-0abc123"


def test_get_resource_detail_not_found_for_other_user(client, make_user, auth_header, db_session):
    owner = make_user(email="owner2@example.com")
    intruder = make_user(email="intruder2@example.com")
    _, _, resource = _setup_aws_ec2(db_session, owner)
    db_session.commit()

    resp = client.get(f"/api/v1/resources/{resource.id}", headers=auth_header(intruder))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
