"""app/metrics.py::get_top_utilization() 단위 테스트(2026-09-19).

같은 실제 리소스(같은 external_resource_id)가 서로 다른 cloud_account에 중복 등록된 경우
(같은 AWS 계정을 credential 여러 개로 등록하는 등, 실사용 중 발견) 최종 상위 목록에 두 번
나오던 버그를 검증한다. `_cpu_map_for_account`를 monkeypatch해서 실제 CSP 호출 없이 확인한다.
"""

from __future__ import annotations

import datetime as dt

import app.metrics as metrics
from app.models import CloudAccount, Resource, ServiceCatalog


def _make_service(db_session, provider, service_code):
    row = ServiceCatalog(provider=provider, service_code=service_code, category="compute", display_name=service_code)
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user_id, provider, external_account_id):
    row = CloudAccount(user_id=user_id, provider=provider, external_account_id=external_account_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_resource(db_session, account, service, **kwargs):
    now = dt.datetime.now(dt.timezone.utc)
    row = Resource(
        cloud_account_id=account.id, service_catalog_id=service.id,
        provider_resource_key=f"{account.provider}:{service.service_code}:{kwargs.get('external_resource_id')}:{account.id}",
        is_stale=False, first_seen_at=now, last_seen_at=now, **kwargs,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_dedupes_same_external_resource_registered_under_two_accounts(db_session, make_user, monkeypatch):
    user = make_user()
    service = _make_service(db_session, "aws", "ec2")
    # cloud_accounts는 (user_id, provider, external_account_id) UNIQUE라 여기서는 서로 다른
    # external_account_id를 쓴다 — 실사용 중 발견한 사고(같은 실제 AWS 계정이 credential
    # 등록 과정에서 서로 다른 cloud_account 행 2개로 남음)를 재현하는 핵심은 "같은
    # external_resource_id를 가진 Resource가 서로 다른 cloud_account에 있다"는 것이지
    # cloud_accounts 자체의 중복이 아니다.
    account1 = _make_account(db_session, user.id, "aws", "111111111111")
    account2 = _make_account(db_session, user.id, "aws", "222222222222")
    # 실사용 중 발견한 그대로 — 같은 실제 EC2 인스턴스가 서로 다른 cloud_account에 등록됨.
    _make_resource(
        db_session, account1, service, name="mcp-test-01", external_resource_id="i-dup123",
        original_resource_type="EC2 Instance",
    )
    _make_resource(
        db_session, account2, service, name="mcp-test-01", external_resource_id="i-dup123",
        original_resource_type="AWS::EC2::Instance",
    )

    monkeypatch.setattr(metrics, "_cpu_map_for_account", lambda db, account, group: {"i-dup123": 0.2})

    items = metrics.get_top_utilization(db_session, user, limit=10)

    assert len(items) == 1
    assert items[0]["cpu_percent"] == 0.2
    assert items[0]["name"] == "mcp-test-01"


def test_keeps_distinct_resources_with_different_external_ids(db_session, make_user, monkeypatch):
    user = make_user()
    service = _make_service(db_session, "aws", "ec2")
    account = _make_account(db_session, user.id, "aws", "111111111111")
    _make_resource(db_session, account, service, name="a", external_resource_id="i-a", original_resource_type="EC2 Instance")
    _make_resource(db_session, account, service, name="b", external_resource_id="i-b", original_resource_type="EC2 Instance")

    monkeypatch.setattr(metrics, "_cpu_map_for_account", lambda db, account, group: {"i-a": 10.0, "i-b": 20.0})

    items = metrics.get_top_utilization(db_session, user, limit=10)

    assert len(items) == 2
    # 정렬은 cpu_percent 내림차순이다.
    assert items[0]["name"] == "b"
    assert items[1]["name"] == "a"


def test_returns_empty_list_when_no_compute_resources(db_session, make_user):
    user = make_user()
    assert metrics.get_top_utilization(db_session, user) == []


def test_unused_resources_includes_unattached_volume_with_known_idle_days(db_session, make_user):
    user = make_user()
    service = _make_service(db_session, "aws", "ec2")
    account = _make_account(db_session, user.id, "aws", "111111111111")
    changed_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)
    _make_resource(
        db_session, account, service, name="vol-idle", external_resource_id="vol-1",
        original_resource_type="EBS Volume", status="AVAILABLE", status_changed_at=changed_at,
        estimated_monthly_cost=None,
    )

    items = metrics.get_unused_resources(db_session, user, limit=10)

    assert len(items) == 1
    assert items[0]["reason"] == "unattached_disk"
    assert items[0]["idle_days"] == 5
    assert items[0]["estimated_monthly_cost"] is None


def test_unused_resources_leaves_idle_days_none_when_status_never_recorded(db_session, make_user):
    user = make_user()
    service = _make_service(db_session, "aws", "ec2")
    account = _make_account(db_session, user.id, "aws", "111111111111")
    # status_changed_at 필드 도입 이전부터 있던 행을 흉내낸다(NULL) — "0일"로 지어내면 안 된다.
    _make_resource(
        db_session, account, service, name="vol-legacy", external_resource_id="vol-2",
        original_resource_type="EBS Volume", status="AVAILABLE", status_changed_at=None,
    )

    items = metrics.get_unused_resources(db_session, user, limit=10)

    assert len(items) == 1
    assert items[0]["idle_days"] is None


def test_unused_resources_excludes_attached_volumes(db_session, make_user):
    user = make_user()
    service = _make_service(db_session, "aws", "ec2")
    account = _make_account(db_session, user.id, "aws", "111111111111")
    _make_resource(
        db_session, account, service, name="vol-in-use", external_resource_id="vol-3",
        original_resource_type="EBS Volume", status="IN-USE",
    )

    assert metrics.get_unused_resources(db_session, user, limit=10) == []


def test_unused_resources_includes_idle_compute_and_excludes_busy(db_session, make_user, monkeypatch):
    user = make_user()
    service = _make_service(db_session, "aws", "ec2")
    account = _make_account(db_session, user.id, "aws", "111111111111")
    _make_resource(
        db_session, account, service, name="idle-vm", external_resource_id="i-idle",
        original_resource_type="EC2 Instance", estimated_monthly_cost=7.5,
    )
    _make_resource(
        db_session, account, service, name="busy-vm", external_resource_id="i-busy",
        original_resource_type="EC2 Instance", estimated_monthly_cost=20,
    )

    monkeypatch.setattr(metrics, "_cpu_map_for_account", lambda db, account, group: {"i-idle": 2.0, "i-busy": 80.0})

    items = metrics.get_unused_resources(db_session, user, limit=10)

    assert len(items) == 1
    assert items[0]["reason"] == "idle_compute"
    assert items[0]["name"] == "idle-vm"
    # 실행 중이었던 기간과 유휴 기간은 다른 질문이라 컴퓨트는 항상 idle_days=None이다.
    assert items[0]["idle_days"] is None
    assert items[0]["estimated_monthly_cost"] == 7.5


def test_unused_resources_sorts_known_cost_first(db_session, make_user, monkeypatch):
    user = make_user()
    service = _make_service(db_session, "aws", "ec2")
    account = _make_account(db_session, user.id, "aws", "111111111111")
    _make_resource(
        db_session, account, service, name="vol-no-cost", external_resource_id="vol-4",
        original_resource_type="EBS Volume", status="AVAILABLE",
    )
    _make_resource(
        db_session, account, service, name="idle-with-cost", external_resource_id="i-idle-2",
        original_resource_type="EC2 Instance", estimated_monthly_cost=15,
    )

    monkeypatch.setattr(metrics, "_cpu_map_for_account", lambda db, account, group: {"i-idle-2": 1.0})

    items = metrics.get_unused_resources(db_session, user, limit=10)

    assert len(items) == 2
    assert items[0]["name"] == "idle-with-cost"
    assert items[1]["name"] == "vol-no-cost"
