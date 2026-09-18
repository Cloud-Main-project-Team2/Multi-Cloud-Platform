"""`app/cost/pricing_sync.py` 단위 검증 — 동기화 리소스 정가 추정(PR 1).

`Resource`는 순수 속성 컨테이너로만 쓴다 — DB 세션에 붙이지 않고 값 대입만 확인한다.
1·3·4·5의 기대값은 `test_pricing.py`에 이미 있는 값과 같은 숫자다 — 두 테스트가 어긋나면
어느 쪽이 틀렸는지 바로 보인다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.cost.pricing_sync import apply_list_price_estimate
from app.models import CloudAccount, Credential, Resource, ServiceCatalog
from app.resource_sync import DiscoveredResource
from app.routers.sync_jobs import _upsert_discovered_resources
from app.security.credential_crypto import encrypt_credential_json

NOW = dt.datetime(2026, 9, 18, 6, 0, 0, tzinfo=dt.timezone.utc)


def _make_service(db_session, provider, service_code, category="compute"):
    row = db_session.query(ServiceCatalog).filter_by(provider=provider, service_code=service_code).one_or_none()
    if row is not None:
        return row
    row = ServiceCatalog(provider=provider, service_code=service_code, category=category, display_name=service_code)
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user, provider="aws", external_account_id="111122223333"):
    row = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account, name="cred"):
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    row = Credential(
        cloud_account_id=account.id,
        name=name,
        encrypted_payload=ciphertext,
        encryption_nonce=nonce,
        encryption_key_version="v1",
        verified=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _disc(service_code: str, spec: dict | None = None, status: str | None = "RUNNING") -> DiscoveredResource:
    return DiscoveredResource(
        service_code=service_code,
        external_resource_id="ext-1",
        original_resource_type="Test Resource",
        name="test",
        region=spec.get("region") if spec else None,
        status=status,
        spec=spec or {},
    )


def _resource(**overrides) -> Resource:
    defaults = dict(
        cloud_account_id=1,
        service_catalog_id=1,
        provider_resource_key="aws:ec2:ext-1",
        external_resource_id="ext-1",
        original_resource_type="Test Resource",
        name="test",
        status="RUNNING",
        estimated_monthly_cost=None,
        cost_currency=None,
        cost_source=None,
        cost_as_of=None,
        raw_metadata=None,
        tags={},
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    defaults.update(overrides)
    return Resource(**defaults)


def test_case1_aws_ec2_known_sku_fills_estimate():
    resource = _resource()
    disc = _disc("ec2", {"instance_type": "t3.medium", "region": "ap-northeast-2"})

    apply_list_price_estimate(resource, "aws", disc, NOW)

    assert resource.estimated_monthly_cost == Decimal("37.96")
    assert resource.cost_source == "list_price_estimate"
    assert resource.cost_currency == "USD"
    assert resource.cost_as_of == NOW


def test_case2_aws_ebs_volume_no_spec_stays_none():
    resource = _resource()
    disc = _disc("ec2", spec={})  # EBS 볼륨은 spec을 받지 않는다(app/providers/aws.py)

    apply_list_price_estimate(resource, "aws", disc, NOW)

    assert resource.estimated_monthly_cost is None
    assert resource.cost_currency is None
    assert resource.cost_source is None
    assert resource.cost_as_of is None


def test_case3_azure_vm_prefix_already_stripped_by_adapter():
    # azure.py 어댑터가 "Standard_" 접두사를 이미 떼고 spec에 넣는다 — pricing_sync는
    # 어댑터가 넘긴 값을 그대로 쓴다.
    resource = _resource()
    disc = _disc("vm", {"instance_type": "B2s", "region": "koreacentral"})

    apply_list_price_estimate(resource, "azure", disc, NOW)

    assert resource.estimated_monthly_cost == Decimal("41.03")


def test_case4_gcp_compute_zone_converted_to_region_by_adapter():
    # gcp.py 어댑터가 machine_type URL과 zone("us-central1-a")을 region("us-central1")으로
    # 변환해 spec에 넣는다.
    resource = _resource()
    disc = _disc("compute_engine", {"instance_type": "e2-medium", "region": "us-central1"})

    apply_list_price_estimate(resource, "gcp", disc, NOW)

    assert resource.estimated_monthly_cost == Decimal("24.46")


def test_case5_gcp_cloud_sql_engine_mapped_by_adapter():
    resource = _resource()
    disc = _disc("cloud_sql", {"engine": "PostgreSQL", "region": "asia-northeast3"})

    apply_list_price_estimate(resource, "gcp", disc, NOW)

    assert resource.estimated_monthly_cost == Decimal("46.06")


def test_case6_seed_resource_is_not_overwritten():
    resource = _resource(
        estimated_monthly_cost=Decimal("12.00"),
        cost_currency="USD",
        cost_source="seed",
        cost_as_of=NOW - dt.timedelta(days=1),
    )
    disc = _disc("ec2", {"instance_type": "t3.medium", "region": "ap-northeast-2"})

    apply_list_price_estimate(resource, "aws", disc, NOW)

    assert resource.estimated_monthly_cost == Decimal("12.00")
    assert resource.cost_source == "seed"
    assert resource.cost_as_of == NOW - dt.timedelta(days=1)


def test_case6_1_actual_cost_resource_is_not_overwritten():
    # PR 4 이후 실측이 들어와 cost_source가 "actual"류 값으로 바뀐 행이라고 가정한다.
    resource = _resource(
        estimated_monthly_cost=Decimal("128.40"),
        cost_currency="USD",
        cost_source="aws_cost_explorer",
        cost_as_of=NOW - dt.timedelta(hours=6),
    )
    disc = _disc("ec2", {"instance_type": "t3.medium", "region": "ap-northeast-2"})

    apply_list_price_estimate(resource, "aws", disc, NOW)

    assert resource.estimated_monthly_cost == Decimal("128.40")
    assert resource.cost_source == "aws_cost_explorer"


def test_case7_raw_metadata_merged_not_overwritten():
    resource = _resource(raw_metadata={"instance_id": "i-0123456789abcdef0"})
    disc = _disc("ec2", {"instance_type": "t3.medium", "region": "ap-northeast-2"})

    apply_list_price_estimate(resource, "aws", disc, NOW)

    assert resource.raw_metadata["instance_id"] == "i-0123456789abcdef0"
    assert resource.raw_metadata["list_price_spec"]["spec"] == disc.spec


def test_case8_estimate_cleared_when_spec_falls_out_of_table():
    resource = _resource(
        estimated_monthly_cost=Decimal("37.96"),
        cost_currency="USD",
        cost_source="list_price_estimate",
        cost_as_of=NOW - dt.timedelta(days=1),
    )
    # 허용 목록 밖 인스턴스 타입으로 바뀌었다고 가정
    disc = _disc("ec2", {"instance_type": "t3.large", "region": "ap-northeast-2"})

    apply_list_price_estimate(resource, "aws", disc, NOW)

    assert resource.estimated_monthly_cost is None
    assert resource.cost_currency is None
    assert resource.cost_source is None
    assert resource.cost_as_of is None


def test_case9_status_changed_at_updates_when_status_differs(db_session, make_user):
    """`_upsert_discovered_resources`를 실제로 두 번 호출해 상태가 바뀐 행만
    `status_changed_at`이 기록되는지 확인한다(§2-5 — existing.status 대입보다 위에 있어야 함)."""
    user = make_user()
    account = _make_account(db_session, user)
    credential = _make_credential(db_session, account)
    _make_service(db_session, "aws", "ec2")

    disc_running = DiscoveredResource(
        service_code="ec2",
        external_resource_id="i-0123456789abcdef0",
        original_resource_type="EC2 Instance",
        name="web-01",
        region="ap-northeast-2",
        status="RUNNING",
    )
    _upsert_discovered_resources(db_session, account, credential, [disc_running])
    resource = db_session.query(Resource).filter_by(external_resource_id="i-0123456789abcdef0").one()
    assert resource.status_changed_at is None  # 신규 행은 first_seen_at이 이미 그 시각이다

    disc_stopped = DiscoveredResource(
        service_code="ec2",
        external_resource_id="i-0123456789abcdef0",
        original_resource_type="EC2 Instance",
        name="web-01",
        region="ap-northeast-2",
        status="STOPPED",
    )
    _upsert_discovered_resources(db_session, account, credential, [disc_stopped])
    db_session.refresh(resource)

    assert resource.status == "STOPPED"
    assert resource.status_changed_at is not None


def test_case10_status_changed_at_unchanged_when_status_same(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    credential = _make_credential(db_session, account)
    _make_service(db_session, "aws", "ec2")

    disc = DiscoveredResource(
        service_code="ec2",
        external_resource_id="i-0123456789abcdef0",
        original_resource_type="EC2 Instance",
        name="web-01",
        region="ap-northeast-2",
        status="RUNNING",
    )
    _upsert_discovered_resources(db_session, account, credential, [disc])
    resource = db_session.query(Resource).filter_by(external_resource_id="i-0123456789abcdef0").one()

    # 같은 상태로 다시 동기화 — 값이 달라지지 않았으므로 기록하지 않는다.
    _upsert_discovered_resources(db_session, account, credential, [disc])
    db_session.refresh(resource)

    assert resource.status == "RUNNING"
    assert resource.status_changed_at is None
