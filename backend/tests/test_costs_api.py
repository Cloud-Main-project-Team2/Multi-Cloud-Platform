"""PR 5 — 비용 조회 API 6종 HTTP 계약 검증(docs/01_API_Specification_v1.2.md §11).

실제 CSP를 호출하지 않는다 — `cloud_account_costs`에 직접 행을 심어 두고 조회만 확인한다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.models import CloudAccount, CloudAccountCost, Credential, ServiceCatalog
from app.security.credential_crypto import encrypt_credential_json


def _make_account(db_session, user, provider="aws", external_account_id="111122223333"):
    account = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id)
    db_session.add(account)
    db_session.flush()
    return account


def _make_credential(db_session, account):
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    credential = Credential(
        cloud_account_id=account.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
        encryption_key_version="v1", verified=True, permission_scope={"cost_read": True},
    )
    db_session.add(credential)
    db_session.flush()
    return credential


def _add_cost_row(db_session, account, day, amount, currency="USD", service="AmazonEC2", charge_category="usage", source="aws_cost_explorer"):
    row = CloudAccountCost(
        cloud_account_id=account.id, provider=account.provider, charge_category=charge_category,
        service=service, amount=Decimal(amount), currency=currency,
        period_start=day, period_end=day + dt.timedelta(days=1),
        as_of=dt.datetime.now(dt.timezone.utc), source=source,
        source_record_key=f"{account.provider}:{account.external_account_id}:{day.isoformat()}:{service}:{charge_category}:{currency}",
    )
    db_session.add(row)
    db_session.flush()
    return row


# --- capabilities ---------------------------------------------------------------------------


def test_capabilities_has_required_three_fields_and_unsupported_for_provider_without_adapter(client, make_user, auth_header, db_session):
    user = make_user()
    aws = _make_account(db_session, user, provider="aws", external_account_id="111122223333")
    _make_credential(db_session, aws)
    gcp = _make_account(db_session, user, provider="gcp", external_account_id="proj-1")   # 아직 수집기 없음

    resp = client.get("/api/v1/costs/capabilities", headers=auth_header(user))

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert "staleness_threshold_hours" in body
    by_provider = {item["provider"]: item for item in body["items"]}
    for item in body["items"]:
        assert "status" in item and "as_of" in item and "ingestion_running" in item  # 필수 3필드

    assert by_provider["aws"]["status"] == "PENDING"
    assert by_provider["gcp"]["status"] == "UNSUPPORTED"
    assert by_provider["gcp"]["status"] != "PERMISSION_DENIED"
    assert by_provider["gcp"]["cost_read"] is None


def test_capabilities_only_shows_own_accounts(client, make_user, auth_header, db_session):
    owner = make_user(email="owner@example.com")
    other = make_user(email="other@example.com")
    _make_account(db_session, owner)
    _make_account(db_session, other)

    resp = client.get("/api/v1/costs/capabilities", headers=auth_header(owner))

    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 1


# --- summary ---------------------------------------------------------------------------------


def test_summary_mixed_currency_gives_two_lines_no_total(client, make_user, auth_header, db_session):
    """USD·KRW 계정이 섞이면 kpis의 각 항목이 2줄이고 합계가 없다(QA-03)."""
    user = make_user()
    usd_account = _make_account(db_session, user, provider="aws", external_account_id="111122223333")
    _make_credential(db_session, usd_account)
    _add_cost_row(db_session, usd_account, dt.date(2026, 9, 13), "100.000000", currency="USD")

    krw_account = _make_account(db_session, user, provider="aws", external_account_id="222233334444")
    _make_credential(db_session, krw_account)
    _add_cost_row(db_session, krw_account, dt.date(2026, 9, 13), "96000.000000", currency="KRW")

    resp = client.get(
        "/api/v1/costs/summary",
        params={"period_start": "2026-09-01", "period_end": "2026-09-18"},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    kpis = resp.json()["data"]["kpis"]
    assert len(kpis["mtd_actual"]) == 2
    amounts = {row["currency"]: row["amount"] for row in kpis["mtd_actual"]}
    assert amounts["USD"] == "100.000000"
    assert amounts["KRW"] == "96000.000000"
    # 340 같은 잘못 합쳐진 숫자가 없다 — 각 줄이 원래 금액 그대로다
    assert "196100" not in str(kpis["mtd_actual"])


def test_summary_connected_empty_vs_pending_are_different_shapes(client, make_user, auth_header, db_session):
    """수집 성공 0건은 CONNECTED_EMPTY이고 금액이 0, 한 번도 수집 안 한 계정은 PENDING이고
    금액이 빈 배열이다 — 둘이 같은 모양이 아니다(QA-01)."""
    from app.models import CostIngestionRun

    user = make_user()
    empty_account = _make_account(db_session, user, external_account_id="111100000000")
    _make_credential(db_session, empty_account)
    db_session.add(
        CostIngestionRun(
            user_id=user.id, cloud_account_id=empty_account.id, trigger_type="manual", status="success",
            period_start=dt.date(2026, 9, 1), period_end=dt.date(2026, 9, 18),
            requested_at=dt.datetime.now(dt.timezone.utc), finished_at=dt.datetime.now(dt.timezone.utc),
            records_replaced=0,
        )
    )
    pending_account = _make_account(db_session, user, external_account_id="222200000000")
    _make_credential(db_session, pending_account)
    db_session.flush()

    resp = client.get(
        "/api/v1/costs/summary",
        params={"period_start": "2026-09-01", "period_end": "2026-09-18", "cloud_account_id": [str(empty_account.id), str(pending_account.id)]},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    accounts_by_id = {a["cloud_account_id"]: a for a in resp.json()["data"]["accounts"]}
    empty = accounts_by_id[str(empty_account.id)]
    pending = accounts_by_id[str(pending_account.id)]
    assert empty["status"] == "CONNECTED_EMPTY"
    assert pending["status"] == "PENDING"
    assert empty["status"] != pending["status"]


def test_summary_rejects_period_over_366_days(client, make_user, auth_header, db_session):
    user = make_user()
    _make_account(db_session, user)

    resp = client.get(
        "/api/v1/costs/summary",
        params={"period_start": "2025-01-01", "period_end": "2026-06-01"},
        headers=auth_header(user),
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_summary_has_required_three_fields_per_account(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)

    resp = client.get(
        "/api/v1/costs/summary",
        params={"period_start": "2026-09-01", "period_end": "2026-09-18"},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    for a in resp.json()["data"]["accounts"]:
        assert "status" in a and "as_of" in a and "ingestion_running" in a
    assert "staleness_threshold_hours" in resp.json()["data"]


# --- breakdown ---------------------------------------------------------------------------------


def test_breakdown_items_plus_rest_plus_unallocated_equals_total(client, make_user, auth_header, db_session):
    """items 합 + rest + unallocated = total이 성립한다(QA-09)."""
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _add_cost_row(db_session, account, dt.date(2026, 9, 10), "74.200000", service="AmazonEC2")
    _add_cost_row(db_session, account, dt.date(2026, 9, 11), "31.100000", service="AmazonRDS")
    _add_cost_row(db_session, account, dt.date(2026, 9, 12), "10.000000", service=None)  # 계정 단위(귀속 없음)

    resp = client.get(
        "/api/v1/costs/breakdown",
        params={"period_start": "2026-09-01", "period_end": "2026-09-18", "dimension": "service", "top_n": 1},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    items_sum = sum(Decimal(i["amount"]) for i in body["items"])
    rest = Decimal(body["rest"]["amount"])
    unallocated = Decimal(body["unallocated"]["amount"])
    total = Decimal(body["total"])
    assert items_sum + rest + unallocated == total
    assert unallocated == Decimal("10.000000")


def test_breakdown_dimension_tag_returns_501(client, make_user, auth_header, db_session):
    user = make_user()
    _make_account(db_session, user)

    resp = client.get(
        "/api/v1/costs/breakdown",
        params={"dimension": "tag", "tag_key": "Owner"},
        headers=auth_header(user),
    )

    assert resp.status_code == 501


# --- trend ---------------------------------------------------------------------------------


def test_trend_does_not_fill_missing_day_with_zero(client, make_user, auth_header, db_session):
    """빠진 날을 0으로 채우지 않는다 — points에서 빼고 missing_days에 넣는다(QA-08)."""
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _add_cost_row(db_session, account, dt.date(2026, 9, 13), "8.000000")
    # 9/14는 의도적으로 비워 둔다
    _add_cost_row(db_session, account, dt.date(2026, 9, 15), "9.000000")

    resp = client.get(
        "/api/v1/costs/trend",
        params={"period_start": "2026-09-13", "period_end": "2026-09-16", "granularity": "daily"},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    all_labels = {p["period_start"] for s in body["series"] for p in s["points"]}
    assert "2026-09-14" not in all_labels
    assert "2026-09-14" in body["missing_days"]


# --- changes ---------------------------------------------------------------------------------


def test_changes_previous_period_is_always_same_length(client, make_user, auth_header, db_session):
    """`compare=previous_period`는 구조상 항상 같은 길이로 자른다 — `comparability.same_length`가 True다.
    (1단계 정정) 같은 길이라고 comparable은 아니다: 여기서는 8/1 행 하나뿐이라 현재·이전 기간 모두
    수집 확인이 없으므로 comparable=false이고 사유에 LENGTH_MISMATCH는 없어야 한다."""
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _add_cost_row(db_session, account, dt.date(2026, 8, 1), "5.000000")

    resp = client.get(
        "/api/v1/costs/changes",
        params={"period_start": "2026-09-01", "period_end": "2026-09-18", "compare": "previous_period"},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["current"]["days"] == body["previous"]["days"]
    assert body["comparability"]["same_length"] is True
    assert "LENGTH_MISMATCH" not in body["comparability"]["reasons"]
    assert body["comparable"] is False
    assert {"CURRENT_COVERAGE", "PREVIOUS_COVERAGE"} <= set(body["comparability"]["reasons"])
    assert body["totals"]["delta"] is None and body["totals"]["delta_pct"] is None


def test_changes_previous_month_not_comparable_when_month_lengths_differ(client, make_user, auth_header, db_session):
    """3월(31일) 전체와 그 전월인 2월(2026년은 평년, 28일)을 비교하지 않는다 —
    comparable:false일 때 증감이 어디에도 보이지 않는다."""
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)

    resp = client.get(
        "/api/v1/costs/changes",
        params={"period_start": "2026-03-01", "period_end": "2026-04-01", "compare": "previous_month"},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["current"]["days"] == 31
    assert body["previous"]["days"] == 28
    assert body["comparable"] is False
    assert "LENGTH_MISMATCH" in body["comparability"]["reasons"]
    assert body["totals"]["previous"] is None  # 행이 없어 통화가 없다 — 있었다면 "확인된 금액"으로 실린다
    assert body["totals"]["delta"] is None
    assert body["increases"] == []
    assert body["new_items"] == []
