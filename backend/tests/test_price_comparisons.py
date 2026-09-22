"""PR 8 — `POST /provisioning/price-comparisons` (명세 v1.2 §10.2 · 확정 12)."""

from __future__ import annotations

from decimal import Decimal

from app.cost.price_compare import pick_tier
from app.models import ServiceCatalog


def _catalog(db, provider="aws", code="ec2", category="compute"):
    row = db.query(ServiceCatalog).filter_by(provider=provider, service_code=code).first()
    if row is None:
        row = ServiceCatalog(provider=provider, service_code=code, category=category, display_name=code, provisionable=True)
        db.add(row)
        db.flush()
    return row


def _body(catalog_id, **spec):
    common = {"region_group": "northeast_asia", "vcpu": 2, "memory_gib": 4, "usage_hours_per_month": 730}
    common.update(spec)
    return {"service_catalog_id": str(catalog_id), "common_spec": common, "providers": ["aws", "azure", "gcp"]}


def test_pick_tier_exact_and_nearest():
    tier, exact = pick_tier(2, Decimal("4"))
    assert tier["key"] == "standard" and exact
    tier, exact = pick_tier(2, Decimal("8"))
    assert tier["key"] == "high" and exact  # aws t3.large 사양과 일치
    tier, exact = pick_tier(3, Decimal("6"))
    assert tier["key"] == "standard" and not exact


def test_price_comparison_contract(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    cat = _catalog(db_session)
    resp = client.post("/api/v1/provisioning/price-comparisons", json=_body(cat.id), headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["currency"] == "USD" and d["as_of"].endswith("Z")
    by = {it["provider"]: it for it in d["items"]}
    assert by["aws"]["sku"] == "t3.medium" and by["azure"]["sku"] == "B2s" and by["gcp"]["sku"] == "e2-medium"
    assert Decimal(by["aws"]["estimated_monthly_cost"]) == Decimal("0.0520") * 730
    for it in d["items"]:
        assert it["cost_kind"] == "list_price_estimate" and it["source"] == "provider_price_catalog"
        assert "730 hours/month" in it["assumptions"]
        assert any("부속 요금 미포함" in a for a in it["assumptions"])
        assert any("유사 사양 매핑 기준" in a for a in it["assumptions"])  # 확정 12
    assert any("2 vCPU · 4 GiB" in a for a in by["azure"]["assumptions"])


def test_price_comparison_unavailable_is_null_not_zero(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    cat = _catalog(db_session)
    resp = client.post("/api/v1/provisioning/price-comparisons", json=_body(cat.id, vcpu=2, memory_gib=8), headers=h)
    by = {it["provider"]: it for it in resp.json()["data"]["items"]}
    assert by["aws"]["sku"] == "t3.large" and by["aws"]["estimated_monthly_cost"] is None  # 정가표에 없다
    assert any(a.startswith("견적 불가") for a in by["aws"]["assumptions"])
    assert by["azure"]["estimated_monthly_cost"] is not None
    # 비-compute 카탈로그도 0이 아니라 null
    s3 = _catalog(db_session, "aws", "s3", "storage_object")
    resp = client.post("/api/v1/provisioning/price-comparisons", json=_body(s3.id), headers=h)
    assert all(it["estimated_monthly_cost"] is None for it in resp.json()["data"]["items"])


def test_price_comparison_hours_scaling_and_validation(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    cat = _catalog(db_session)
    resp = client.post("/api/v1/provisioning/price-comparisons", json=_body(cat.id, usage_hours_per_month=365), headers=h)
    aws = [it for it in resp.json()["data"]["items"] if it["provider"] == "aws"][0]
    assert Decimal(aws["estimated_monthly_cost"]) == (Decimal("0.0520") * 730 / 730 * 365).quantize(Decimal("0.000001"))
    assert "365 hours/month" in aws["assumptions"]
    assert client.post("/api/v1/provisioning/price-comparisons", json=_body(cat.id, region_group="mars"), headers=h).status_code == 422
    assert client.post("/api/v1/provisioning/price-comparisons", json=_body(999999), headers=h).status_code == 404
    assert client.post("/api/v1/provisioning/price-comparisons", json=_body(cat.id)).status_code == 401
