"""PR 4 — AWS Cost Explorer 어댑터(boto3 스텁) + 범위 교체 저장(`app/cost/ingest.py`) 검증.

⚠️ 실제 boto3를 절대 호출하지 않는다 — Cost Explorer는 요청당 $0.01이라 테스트가 부르면
CI를 돌릴 때마다 돈이 나간다(docs/비용_개발문서/08_백엔드_구현가이드.md §9).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

import app.cost.aws_cost as aws_cost_module
from app.cost.aws_cost import AwsCostProvider
from app.cost.base import CostRow
from app.cost.ingest import replace_cost_rows
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun


class _FakeCeClient:
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls: list[dict] = []

    def get_cost_and_usage(self, **kwargs):
        self.calls.append(kwargs)
        return self._pages.pop(0)


def _install_fake_client(monkeypatch, pages=None, client=None):
    fake = client if client is not None else _FakeCeClient(pages or [])
    monkeypatch.setattr(aws_cost_module.boto3, "client", lambda *a, **k: fake)
    return fake


def _usage_group(service: str, record_type: str, amount: str, unit: str = "USD") -> dict:
    return {"Keys": [service, record_type], "Metrics": {"UnblendedCost": {"Amount": amount, "Unit": unit}}}


# --- AwsCostProvider.fetch() -------------------------------------------------------------


def test_fetch_maps_record_type_to_charge_category(monkeypatch):
    page = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-09-01", "End": "2026-09-02"},
                "Estimated": False,
                "Groups": [
                    _usage_group("AmazonEC2", "Usage", "10.500000"),
                    _usage_group("AmazonEC2", "Credit", "-2.000000"),
                    _usage_group("AmazonRDS", "Refund", "1.000000"),
                    _usage_group("AmazonS3", "Tax", "0.500000"),
                    _usage_group("AWSSupport", "SomeNewRecordType", "5.000000"),
                ],
            }
        ]
    }
    _install_fake_client(monkeypatch, [page])

    result = AwsCostProvider().fetch(
        {"access_key_id": "AKIA", "secret_access_key": "s"}, "111122223333",
        dt.date(2026, 9, 1), dt.date(2026, 9, 2),
    )

    by_category = {r.charge_category: r for r in result.rows}
    assert by_category["usage"].amount == Decimal("10.500000")
    assert by_category["credit"].amount == Decimal("-2.000000")
    assert by_category["refund"].amount == Decimal("1.000000")
    assert by_category["tax"].amount == Decimal("0.500000")
    # 모르는 RECORD_TYPE은 usage로 새지 않고 other로 간다
    assert by_category["other"].amount == Decimal("5.000000")
    assert result.api_calls == 1
    assert result.partial is False
    assert result.currency == "USD"


def test_fetch_follows_next_page_token_until_exhausted(monkeypatch):
    page1 = {
        "ResultsByTime": [
            {"TimePeriod": {"Start": "2026-09-01", "End": "2026-09-02"}, "Groups": [_usage_group("AmazonEC2", "Usage", "1.000000")]}
        ],
        "NextPageToken": "token-2",
    }
    page2 = {
        "ResultsByTime": [
            {"TimePeriod": {"Start": "2026-09-02", "End": "2026-09-03"}, "Groups": [_usage_group("AmazonEC2", "Usage", "2.000000")]}
        ]
    }
    fake = _install_fake_client(monkeypatch, [page1, page2])

    result = AwsCostProvider().fetch(
        {"access_key_id": "AKIA", "secret_access_key": "s"}, "111122223333",
        dt.date(2026, 9, 1), dt.date(2026, 9, 3),
    )

    assert result.api_calls == 2
    assert len(result.rows) == 2
    assert "NextPageToken" not in fake.calls[0]
    assert fake.calls[1]["NextPageToken"] == "token-2"


def test_fetch_returns_partial_on_client_error_after_first_page(monkeypatch):
    page1 = {
        "ResultsByTime": [
            {"TimePeriod": {"Start": "2026-09-01", "End": "2026-09-02"}, "Groups": [_usage_group("AmazonEC2", "Usage", "1.000000")]}
        ],
        "NextPageToken": "token-2",
    }

    class _FlakyClient(_FakeCeClient):
        def get_cost_and_usage(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return page1
            raise ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "GetCostAndUsage"
            )

    _install_fake_client(monkeypatch, client=_FlakyClient([]))

    result = AwsCostProvider().fetch(
        {"access_key_id": "AKIA", "secret_access_key": "s"}, "111122223333",
        dt.date(2026, 9, 1), dt.date(2026, 9, 3),
    )

    assert result.partial is True
    assert result.error_code == "PROVIDER_RATE_LIMITED"
    assert len(result.rows) == 1  # 이미 받은 첫 페이지는 버리지 않는다 — 부분 합계를 전체로 보고하지 않는다


def test_fetch_access_denied_maps_to_permission_denied(monkeypatch):
    class _DeniedClient(_FakeCeClient):
        def get_cost_and_usage(self, **kwargs):
            self.calls.append(kwargs)
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "nope"}}, "GetCostAndUsage")

    _install_fake_client(monkeypatch, client=_DeniedClient([]))

    result = AwsCostProvider().fetch(
        {"access_key_id": "AKIA", "secret_access_key": "s"}, "111122223333",
        dt.date(2026, 9, 1), dt.date(2026, 9, 2),
    )

    assert result.partial is True
    assert result.error_code == "CLOUD_PERMISSION_DENIED"
    assert result.rows == []


def test_source_record_key_format(monkeypatch):
    page = {
        "ResultsByTime": [
            {"TimePeriod": {"Start": "2026-09-16", "End": "2026-09-17"}, "Groups": [_usage_group("AmazonEC2", "Usage", "1.000000")]}
        ]
    }
    _install_fake_client(monkeypatch, [page])

    result = AwsCostProvider().fetch(
        {"access_key_id": "AKIA", "secret_access_key": "s"}, "123456789012",
        dt.date(2026, 9, 16), dt.date(2026, 9, 17),
    )

    assert result.rows[0].source_record_key == "aws:123456789012:2026-09-16:AmazonEC2:Usage"


# --- app/cost/ingest.py 범위 교체 --------------------------------------------------------


def _make_account(db_session, user, external_account_id="111122223333") -> CloudAccount:
    account = CloudAccount(user_id=user.id, provider="aws", external_account_id=external_account_id)
    db_session.add(account)
    db_session.flush()
    return account


def _make_run(db_session, user, account, period_start, period_end) -> CostIngestionRun:
    run = CostIngestionRun(
        user_id=user.id,
        cloud_account_id=account.id,
        trigger_type="manual",
        status="running",
        period_start=period_start,
        period_end=period_end,
        requested_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    return run


def test_replace_cost_rows_is_idempotent(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    run = _make_run(db_session, user, account, dt.date(2026, 9, 1), dt.date(2026, 9, 18))

    rows = [
        CostRow(
            period_start=dt.date(2026, 9, 13), period_end=dt.date(2026, 9, 14),
            service="AmazonRDS", charge_category="usage", amount=Decimal("18.400000"),
            currency="USD", is_estimated=False,
            source_record_key="aws:111122223333:2026-09-13:AmazonRDS:Usage",
        )
    ]

    n1 = replace_cost_rows(db_session, account, run, rows, source="aws_cost_explorer")
    db_session.flush()
    n2 = replace_cost_rows(db_session, account, run, rows, source="aws_cost_explorer")
    db_session.flush()

    assert n1 == 1
    assert n2 == 1
    total = (
        db_session.query(CloudAccountCost)
        .filter(CloudAccountCost.cloud_account_id == account.id)
        .count()
    )
    assert total == 1  # 같은 범위를 두 번 수집해도 두 배가 되지 않는다(QA-10)


def test_replace_cost_rows_only_touches_its_own_period_and_source(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)

    # 다른 기간(8월) 행 — 건드리면 안 된다
    db_session.add(
        CloudAccountCost(
            cloud_account_id=account.id, provider="aws", charge_category="usage", amount=Decimal("5.000000"),
            currency="USD", period_start=dt.date(2026, 8, 15), period_end=dt.date(2026, 8, 16),
            as_of=dt.datetime.now(dt.timezone.utc), source="aws_cost_explorer",
            source_record_key="aws:111122223333:2026-08-15::Usage",
        )
    )
    # 다른 source — 건드리면 안 된다
    db_session.add(
        CloudAccountCost(
            cloud_account_id=account.id, provider="aws", charge_category="usage", amount=Decimal("7.000000"),
            currency="USD", period_start=dt.date(2026, 9, 5), period_end=dt.date(2026, 9, 6),
            as_of=dt.datetime.now(dt.timezone.utc), source="manual_import",
            source_record_key="aws:111122223333:2026-09-05::manual",
        )
    )
    db_session.flush()

    run = _make_run(db_session, user, account, dt.date(2026, 9, 1), dt.date(2026, 9, 18))
    rows = [
        CostRow(
            period_start=dt.date(2026, 9, 10), period_end=dt.date(2026, 9, 11),
            service="AmazonEC2", charge_category="usage", amount=Decimal("3.000000"),
            currency="USD", is_estimated=False,
            source_record_key="aws:111122223333:2026-09-10:AmazonEC2:Usage",
        )
    ]
    replace_cost_rows(db_session, account, run, rows, source="aws_cost_explorer")
    db_session.flush()

    remaining = {
        (row.period_start, row.source): row.amount
        for row in db_session.query(CloudAccountCost).filter(CloudAccountCost.cloud_account_id == account.id)
    }
    assert remaining[(dt.date(2026, 8, 15), "aws_cost_explorer")] == Decimal("5.000000")
    assert remaining[(dt.date(2026, 9, 5), "manual_import")] == Decimal("7.000000")
    assert remaining[(dt.date(2026, 9, 10), "aws_cost_explorer")] == Decimal("3.000000")


def test_cloud_account_costs_allows_negative_amount_via_ingest(db_session, make_user):
    """크레딧이 섞인 날에 usage와 credit이 각각 한 번씩만 반영된다(QA-13)."""
    user = make_user()
    account = _make_account(db_session, user)
    run = _make_run(db_session, user, account, dt.date(2026, 9, 1), dt.date(2026, 9, 18))

    rows = [
        CostRow(
            period_start=dt.date(2026, 9, 13), period_end=dt.date(2026, 9, 14),
            service="AmazonEC2", charge_category="usage", amount=Decimal("20.000000"),
            currency="USD", is_estimated=False,
            source_record_key="aws:111122223333:2026-09-13:AmazonEC2:Usage",
        ),
        CostRow(
            period_start=dt.date(2026, 9, 13), period_end=dt.date(2026, 9, 14),
            service="AmazonEC2", charge_category="credit", amount=Decimal("-5.000000"),
            currency="USD", is_estimated=False,
            source_record_key="aws:111122223333:2026-09-13:AmazonEC2:Credit",
        ),
    ]
    replace_cost_rows(db_session, account, run, rows, source="aws_cost_explorer")
    db_session.flush()

    saved = (
        db_session.query(CloudAccountCost)
        .filter(CloudAccountCost.cloud_account_id == account.id)
        .order_by(CloudAccountCost.charge_category)
        .all()
    )
    assert len(saved) == 2
    by_category = {row.charge_category: row.amount for row in saved}
    assert by_category["usage"] == Decimal("20.000000")
    assert by_category["credit"] == Decimal("-5.000000")
