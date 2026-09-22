"""1단계(2026-09-21) 비용 조회 정확성 — 수집 확인(coverage)·전망 게이트·비교 정합성·통화 필터·UTC.

각 테스트의 docstring에 "왜 이 결과가 옳은가"를 적었다. 기대값을 새 구현에 맞춘 것이 아니라
03 §5(정상 0 ≠ null · 미수집일을 0으로 평균 내지 않음) · QA-01/05/06/08 · 08 §4-4(partial은 저장 안 함)
· coverage.py(행 OR success run 범위, UTC)에서 나온 값이다.

날짜는 `app.cost.query.utc_today`·`app.cost.coverage.utc_today`를 함께 고정한다 — 서버 시계에 따라
"오늘"이 달라지면 완료/미완료 판정이 흔들린다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.cost import query as q_mod
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Credential
from app.security.credential_crypto import encrypt_credential_json

TODAY = dt.date(2026, 9, 21)   # UTC "오늘" — 9월은 30일, 이달 1일~어제 = 9/1~9/20(20일)
NOW = dt.datetime(2026, 9, 21, 3, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _freeze_utc_today(monkeypatch):
    monkeypatch.setattr(q_mod, "utc_today", lambda: TODAY)
    import app.cost.coverage as cov
    monkeypatch.setattr(cov, "utc_today", lambda: TODAY)


# --- 시드 도우미 ------------------------------------------------------------------------------


def _account(db, user, provider="aws", ext="111122223333", label=None):
    a = CloudAccount(user_id=user.id, provider=provider, external_account_id=ext, account_label=label or ext)
    db.add(a)
    db.flush()
    if provider == "aws":
        ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
        db.add(Credential(cloud_account_id=a.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
                          encryption_key_version="v1", verified=True, permission_scope={"cost_read": True}))
        db.flush()
    return a


def _row(db, account, day, amount, currency="USD", service="AmazonEC2", charge_category="usage"):
    db.add(CloudAccountCost(
        cloud_account_id=account.id, provider=account.provider, charge_category=charge_category,
        service=service, amount=Decimal(amount), currency=currency,
        period_start=day, period_end=day + dt.timedelta(days=1), as_of=NOW, source="aws_cost_explorer",
        source_record_key=f"{account.id}:{day.isoformat()}:{service or '-'}:{charge_category}:{currency}",
    ))
    db.flush()


def _run(db, user, account, start, end, status="success", records=1, error_code=None, finished=NOW):
    db.add(CostIngestionRun(
        user_id=user.id, cloud_account_id=account.id, trigger_type="auto", status=status,
        period_start=start, period_end=end, requested_at=finished, started_at=finished, finished_at=finished,
        api_calls=1, records_replaced=records, error_code=error_code,
    ))
    db.flush()


def _rows_every_day(db, account, start, end, amount="1.000000", currency="USD"):
    d = start
    while d < end:
        _row(db, account, d, amount, currency=currency)
        d += dt.timedelta(days=1)


def _summary(client, user, auth_header, **params):
    base = {"period_start": "2026-09-01", "period_end": "2026-09-22"}
    base.update(params)
    resp = client.get("/api/v1/costs/summary", params=base, headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _changes(client, user, auth_header, **params):
    base = {"period_start": "2026-09-01", "period_end": "2026-09-11", "compare": "previous_period"}
    base.update(params)
    resp = client.get("/api/v1/costs/changes", params=base, headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _acc(data, account):
    return [a for a in data["accounts"] if a["cloud_account_id"] == str(account.id)][0]


# --- 1·2·4: 행 없음 + success run = 확인된 0원 / run 없음 = 결측 / partial만 = 미확인 -----------


def test_success_run_without_rows_counts_as_covered_zero(client, make_user, auth_header, db_session):
    """성공한 수집이 $0이면 행이 안 생긴다(coverage.py 머리). 행 유무만 보면 CONNECTED_EMPTY 계정이
    영원히 결측이므로, success run 범위에 든 날은 확인된 날이다. 결측 0 · 합계 제외 아님."""
    user = make_user()
    a = _account(db_session, user)
    _run(db_session, user, a, dt.date(2026, 9, 1), dt.date(2026, 9, 21), records=0)

    d = _summary(client, user, auth_header)
    cov = _acc(d, a)["coverage"]
    assert cov["days"] == 20 and cov["covered"] == 20 and cov["missing_count"] == 0
    assert cov["pending_days"] == 1                   # 9/21(오늘)은 아직 판정 대상이 아니다
    assert d["warnings"] == []
    assert d["excluded"]["accounts"] == 0             # 확인된 0원은 "제외"가 아니다(QA-01)
    assert _acc(d, a)["actual"] is None               # 행이 없으니 금액 자체는 없다 — 0으로 지어내지 않는다


def test_no_run_no_rows_is_missing(client, make_user, auth_header, db_session):
    """수집 흔적이 전혀 없는 날은 결측이다. 계정 상태가 PENDING이면 제외 사유도 PENDING."""
    user = make_user()
    a = _account(db_session, user)

    d = _summary(client, user, auth_header)
    cov = _acc(d, a)["coverage"]
    assert cov["missing_count"] == 20 and cov["covered"] == 0
    assert d["warnings"][0]["missing_count"] == 20
    assert d["warnings"][0]["accounts"] == [{"cloud_account_id": str(a.id), "missing_count": 20}]
    assert d["excluded"]["reason_counts"] == {"PENDING": 1}


def test_partial_success_run_does_not_cover(client, make_user, auth_header, db_session):
    """partial_success는 행을 저장하지 않는다(08 §4-4) — 그 구간은 확인된 것이 아니다. 이번 달 실측
    행이 하나도 없으니(수집 누락 자체는 전망을 막지 않지만) 통화를 알 수 없어 전망은 못 낸다."""
    user = make_user()
    a = _account(db_session, user)
    _run(db_session, user, a, dt.date(2026, 9, 1), dt.date(2026, 9, 21), status="partial_success", records=0)

    d = _summary(client, user, auth_header)
    assert _acc(d, a)["status"] == "CONNECTED_PARTIAL"
    assert _acc(d, a)["coverage"]["missing_count"] == 20
    assert d["kpis"]["forecast_status"]["state"] == "currency_unknown"


# --- 3: 같은 날 A 수집·B 미수집 → 경고에 B만 ----------------------------------------------------


def test_missing_is_per_account_not_union_of_rows(client, make_user, auth_header, db_session):
    """예전엔 어느 계정이든 행이 있으면 그 날이 '수집됨'이었다. A가 9/1~9/20 전부 있고 B가 9/10 하루만
    빠졌다면 결측은 9/10 하루이고, 그 책임은 B다."""
    user = make_user()
    a = _account(db_session, user, ext="A")
    b = _account(db_session, user, ext="B")
    _rows_every_day(db_session, a, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    _rows_every_day(db_session, b, dt.date(2026, 9, 1), dt.date(2026, 9, 10))
    _rows_every_day(db_session, b, dt.date(2026, 9, 11), dt.date(2026, 9, 21))

    d = _summary(client, user, auth_header)
    w = d["warnings"][0]
    assert w["missing_days"] == ["2026-09-10"] and w["missing_count"] == 1
    assert w["accounts"] == [{"cloud_account_id": str(b.id), "missing_count": 1}]
    assert _acc(d, a)["coverage"]["missing_count"] == 0
    assert d["excluded"]["accounts"] == 0             # B는 행이 있으니 합계에 들어 있다 — 부족분은 coverage가 말한다


# --- 5·6: 마지막 run 실패 + 과거 완전 / PENDING 포함 --------------------------------------------


def test_last_run_failed_but_period_fully_covered_forecasts_and_compares(client, make_user, auth_header, db_session):
    """마지막 수집이 실패했어도 조회 기간의 데이터는 완전할 수 있다. capability는 COLLECT_FAILED지만
    coverage는 완전 → 전망 계산·합계 포함. 상태와 기간 완전성은 별개다."""
    user = make_user()
    a = _account(db_session, user)
    _rows_every_day(db_session, a, dt.date(2026, 8, 1), dt.date(2026, 9, 21), amount="2.000000")
    _run(db_session, user, a, dt.date(2026, 8, 1), dt.date(2026, 9, 21), records=51, finished=NOW - dt.timedelta(days=1))
    _run(db_session, user, a, dt.date(2026, 9, 21), dt.date(2026, 9, 22), status="failed", records=0,
         error_code="PROVIDER_API_ERROR", finished=NOW)

    d = _summary(client, user, auth_header)
    assert _acc(d, a)["status"] == "COLLECT_FAILED"
    assert _acc(d, a)["coverage"]["missing_count"] == 0
    assert d["excluded"]["accounts"] == 0
    fs = d["kpis"]["forecast_status"]
    assert fs["state"] == "computed" and fs["based_through"] == "2026-09-20"
    # 9/1~9/20 = $40 → ÷20일 × 30일 = $60
    assert d["kpis"]["forecast_month_end"] == [{"cost_kind": "actual", "currency": "USD", "amount": "60.000000",
                                                "method": "mtd_prorated", "based_through": "2026-09-20"}]

    c = _changes(client, user, auth_header, period_start="2026-09-01", period_end="2026-09-11")
    assert c["comparable"] is True and c["comparability"]["reasons"] == []
    assert c["totals"] == {"current": "20.000000", "previous": "20.000000", "delta": "0.000000", "delta_pct": "0.0"}


def test_pending_account_in_scope_does_not_block_forecast(client, make_user, auth_header, db_session):
    """수집된 적 없는 계정을 조용히 빼고 계산하지 않는다 — 그 계정도 required_accounts·
    incomplete_accounts에 그대로 남는다. 다만 2026-09-22 결정으로 그 계정의 결측이 계산 자체를
    막지는 않는다 — A만으로 누적·경과일수 기준 전망을 낸다(9/1~9/20 $20 ÷ 20일 × 30일 = $30)."""
    user = make_user()
    a = _account(db_session, user, ext="A")
    b = _account(db_session, user, ext="B-pending")
    _rows_every_day(db_session, a, dt.date(2026, 9, 1), dt.date(2026, 9, 21))

    d = _summary(client, user, auth_header)
    fs = d["kpis"]["forecast_status"]
    assert fs["state"] == "computed" and fs["required_accounts"] == 2
    assert fs["incomplete_accounts"] == [{"cloud_account_id": str(b.id), "missing_count": 20}]
    assert d["kpis"]["forecast_month_end"] == [{"cost_kind": "actual", "currency": "USD", "amount": "30.000000",
                                                "method": "mtd_prorated", "based_through": "2026-09-20"}]
    assert d["kpis"]["mtd_actual"][0]["amount"] == "20.000000"   # 합계·전망 모두 실측이 있는 A로만 계산된다


# --- 7: 오늘 포함 기간 vs 어제까지 완료된 기간 ---------------------------------------------------


def test_period_including_today_is_not_comparable_but_completed_period_is(client, make_user, auth_header, db_session):
    """오늘은 미완성 구간이다. 오늘을 포함한 요청은 INCOMPLETE_PERIOD로 비교하지 않고, 기간을 조용히 잘라
    다른 요청으로 바꾸지도 않는다(days는 요청 그대로). 어제까지로 요청하면 정상 비교."""
    user = make_user()
    a = _account(db_session, user)
    _rows_every_day(db_session, a, dt.date(2026, 8, 20), dt.date(2026, 9, 21))

    c = _changes(client, user, auth_header, period_start="2026-09-12", period_end="2026-09-22")
    assert c["comparable"] is False
    assert c["comparability"]["reasons"] == ["INCOMPLETE_PERIOD"]
    assert c["current"]["days"] == 10 and c["totals"]["delta"] is None
    assert c["totals"]["current"] == "9.000000"     # 확인된 금액은 불완전 표시와 함께 쓸 수 있게 그대로 준다

    c2 = _changes(client, user, auth_header, period_start="2026-09-11", period_end="2026-09-21")
    assert c2["comparable"] is True and c2["comparability"]["completed_period"] is True


# --- 8: 월 경계 · KST 자정 전후 -------------------------------------------------------------------


def test_utc_today_is_used_not_server_local(client, make_user, auth_header, db_session, monkeypatch):
    """KST 01:00 = UTC 전날 16:00. 서버 로컬 날짜(KST 9/22)를 쓰면 UTC로 아직 진행 중인 9/21이
    결측으로 잡히고 전망 분모가 하루 늘어난다. utc_today()를 쓰므로 9/21은 pending이다."""
    import app.cost.coverage as cov
    kst_midnight_plus1 = dt.datetime(2026, 9, 21, 16, 0, tzinfo=dt.timezone.utc)   # KST 9/22 01:00
    monkeypatch.setattr(cov, "utc_today", lambda: kst_midnight_plus1.date())
    monkeypatch.setattr(q_mod, "utc_today", lambda: kst_midnight_plus1.date())
    user = make_user()
    a = _account(db_session, user)
    _rows_every_day(db_session, a, dt.date(2026, 9, 1), dt.date(2026, 9, 21))

    d = _summary(client, user, auth_header, period_start="2026-09-01", period_end="2026-09-23")
    cov_ = _acc(d, a)["coverage"]
    assert cov_["end"] == "2026-09-21" and cov_["missing_count"] == 0 and cov_["pending_days"] == 2


def test_month_boundary_forecast_only_for_current_month_window(client, make_user, auth_header, db_session, monkeypatch):
    """10/1(UTC)에는 전망을 내지 않는다(first_day). 9월 조회는 not_current_month."""
    import app.cost.coverage as cov
    monkeypatch.setattr(cov, "utc_today", lambda: dt.date(2026, 10, 1))
    monkeypatch.setattr(q_mod, "utc_today", lambda: dt.date(2026, 10, 1))
    user = make_user()
    a = _account(db_session, user)
    _rows_every_day(db_session, a, dt.date(2026, 9, 1), dt.date(2026, 10, 1))

    d = _summary(client, user, auth_header, period_start="2026-10-01", period_end="2026-10-02")
    assert d["kpis"]["forecast_status"]["state"] == "first_day"
    d2 = _summary(client, user, auth_header, period_start="2026-09-01", period_end="2026-10-01")
    assert d2["kpis"]["forecast_status"]["state"] == "not_current_month"
    assert _acc(d2, a)["coverage"]["missing_count"] == 0 and _acc(d2, a)["coverage"]["days"] == 30


# --- 9: 31일을 넘는 결측 — 개수는 전체, 목록은 31개, 잘림 표시 ------------------------------------


def test_missing_days_list_truncated_but_count_is_full(client, make_user, auth_header, db_session):
    user = make_user()
    a = _account(db_session, user)

    d = _summary(client, user, auth_header, period_start="2026-07-01", period_end="2026-09-01")
    cov = _acc(d, a)["coverage"]
    assert cov["days"] == 62 and cov["missing_count"] == 62
    assert len(cov["missing_days"]) == 31 and cov["truncated"] is True
    assert d["warnings"][0]["missing_count"] == 62 and len(d["warnings"][0]["missing_days"]) == 62   # 합집합은 전체


# --- 10·11: 통화 미확인 vs 통화 필터 제외 · 다통화 전망 -------------------------------------------


def test_currency_filter_excludes_only_accounts_with_known_other_currency(client, make_user, auth_header, db_session):
    """USD 필터: KRW 계정은 CURRENCY_FILTERED(상태는 그대로 CONNECTED_OK), 통화를 아직 모르는 계정은
    불일치로 단정하지 않는다(PENDING 그대로). 같은 계정이 두 사유로 세어지지 않는다."""
    user = make_user()
    usd = _account(db_session, user, ext="USD")
    krw = _account(db_session, user, ext="KRW")
    unknown = _account(db_session, user, ext="UNKNOWN")
    azure = _account(db_session, user, provider="azure", ext="AZ")
    _rows_every_day(db_session, usd, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="1.000000")
    _rows_every_day(db_session, krw, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="1000.000000", currency="KRW")
    _run(db_session, user, usd, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    _run(db_session, user, krw, dt.date(2026, 9, 1), dt.date(2026, 9, 21))

    d = _summary(client, user, auth_header, currency="USD")
    assert d["kpis"]["mtd_actual"] == [{"cost_kind": "actual", "currency": "USD", "amount": "20.000000",
                                        "is_estimated": False, "basis": "usage_before_credits"}]
    k = _acc(d, krw)
    assert k["status"] == "CONNECTED_OK" and k["filter_excluded"] == "currency" and k["actual"] is None
    assert k["coverage"] is None                       # 조회 범위 밖 — 결측으로 세지 않는다
    u = _acc(d, unknown)
    assert u["filter_excluded"] is None and u["currency"] is None and u["status"] == "PENDING"
    assert d["excluded"]["accounts"] == 3
    assert d["excluded"]["reason_counts"] == {"CURRENCY_FILTERED": 1, "PENDING": 1, "UNSUPPORTED": 1}
    # 통화 필터 없이는 두 통화가 두 줄로, 제외는 미지원·PENDING만
    d2 = _summary(client, user, auth_header)
    assert {r["currency"] for r in d2["kpis"]["mtd_actual"]} == {"USD", "KRW"}
    assert d2["excluded"]["reason_counts"] == {"PENDING": 1, "UNSUPPORTED": 1}


def test_multi_currency_forecast_computes_per_currency_despite_partial_coverage(client, make_user, auth_header, db_session):
    """2026-09-22 결정: 수집 누락이 있어도 전망 계산 자체를 막지 않는다. KRW 계정이 하루 부족해도
    USD 전망은 그대로 나오고, KRW도 실제로 수집된 19일치 누적으로 계산된다(0으로 채우지 않는다).
    forecast_status.incomplete_accounts는 어느 계정이 얼마나 빠졌는지 안내만 한다."""
    user = make_user()
    usd = _account(db_session, user, ext="USD")
    krw = _account(db_session, user, ext="KRW")
    _rows_every_day(db_session, usd, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    _rows_every_day(db_session, krw, dt.date(2026, 9, 1), dt.date(2026, 9, 20), currency="KRW")  # 9/20 없음

    d = _summary(client, user, auth_header)
    fs = d["kpis"]["forecast_status"]
    assert fs["state"] == "computed"
    assert fs["incomplete_accounts"] == [{"cloud_account_id": str(krw.id), "missing_count": 1}]
    rows = {r["currency"]: r["amount"] for r in d["kpis"]["forecast_month_end"]}
    assert rows == {"USD": "30.000000", "KRW": "28.500000"}   # USD $20÷20일×30일 · KRW 19÷20일×30일(9/20 제외)

    _row(db_session, krw, dt.date(2026, 9, 20), "1.000000", currency="KRW")
    d2 = _summary(client, user, auth_header)
    assert {r["currency"] for r in d2["kpis"]["forecast_month_end"]} == {"USD", "KRW"}
    assert d2["kpis"]["forecast_status"]["state"] == "computed"


# --- 12: 미분류 금액이 비교 합계에 들어가고 summary와 같다 ---------------------------------------


def test_changes_totals_include_unallocated_and_match_summary(client, make_user, auth_header, db_session):
    """service IS NULL(계정 단위 지원비 등, QA-12)을 버리면 CF-024 총액이 CF-002와 어긋난다(QA-09).
    미분류는 내부 키 __unallocated__·라벨 '미분류'로 항목에도 보이고, 기타(상위 N 초과)와 다르다."""
    user = make_user()
    a = _account(db_session, user)
    d0 = dt.date(2026, 9, 1)
    for i in range(10):
        _row(db_session, a, d0 + dt.timedelta(days=i), "1.000000")
        _row(db_session, a, d0 + dt.timedelta(days=i), "0.500000", service=None)
    for i in range(10):
        _row(db_session, a, dt.date(2026, 8, 22) + dt.timedelta(days=i), "1.000000")

    c = _changes(client, user, auth_header, period_start="2026-09-01", period_end="2026-09-11")
    s = _summary(client, user, auth_header, period_start="2026-09-01", period_end="2026-09-11")
    assert c["comparable"] is True
    assert c["totals"]["current"] == s["kpis"]["mtd_actual"][0]["amount"] == "15.000000"
    new = [i for i in c["new_items"] if i["key"] == "__unallocated__"]
    assert new and new[0]["label"] == "미분류" and new[0]["current"] == "5.000000"
    assert Decimal(c["totals"]["current"]) == sum(Decimal(i["current"]) for i in c["increases"] + c["decreases"] + c["new_items"])


# --- collection-status가 조회 기간 기준 coverage를 준다 ----------------------------------------


def test_collection_status_reports_period_coverage(client, make_user, auth_header, db_session):
    user = make_user()
    a = _account(db_session, user)
    _rows_every_day(db_session, a, dt.date(2026, 9, 1), dt.date(2026, 9, 15))

    resp = client.get("/api/v1/costs/collection-status", params={"period_start": "2026-09-01", "period_end": "2026-09-22"},
                      headers=auth_header(user))
    item = resp.json()["data"]["items"][0]
    # 행은 9/1~9/14, 완료된 날은 9/1~9/20 → 결측 6일(9/15~9/20). 9/21(오늘)은 pending.
    assert item["missing_count"] == 6
    assert item["missing_days"] == [f"2026-09-{d}" for d in range(15, 21)]
    assert item["coverage"]["days"] == 20 and item["coverage"]["covered"] == 14 and item["coverage"]["pending_days"] == 1


# --- 전망 창: period_end=오늘(어제까지)과 오늘+1(오늘 포함)은 같은 전망 ---------------------------


def test_forecast_window_accepts_period_end_today_or_tomorrow(client, make_user, auth_header, db_session):
    """전망 근거는 이달 1일~UTC 어제뿐이라 period_end가 9/21(어제까지)이든 9/22(오늘 포함)이든 같은 값·같은
    based_through여야 한다. 9/20(어제 전)까지면 진행 중 구간 조회가 아니므로 not_current_month, 미래(9/23)면 내지 않는다."""
    user = make_user()
    a = _account(db_session, user)
    _rows_every_day(db_session, a, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="3.000000")

    yesterday_incl = _summary(client, user, auth_header, period_start="2026-09-01", period_end="2026-09-21")
    today_incl = _summary(client, user, auth_header, period_start="2026-09-01", period_end="2026-09-22")
    assert yesterday_incl["kpis"]["forecast_status"]["state"] == today_incl["kpis"]["forecast_status"]["state"] == "computed"
    assert yesterday_incl["kpis"]["forecast_month_end"] == today_incl["kpis"]["forecast_month_end"]
    assert today_incl["kpis"]["forecast_month_end"][0]["amount"] == "90.000000"      # $60 ÷ 20일 × 30일
    assert today_incl["kpis"]["forecast_month_end"][0]["based_through"] == "2026-09-20"

    earlier = _summary(client, user, auth_header, period_start="2026-09-01", period_end="2026-09-20")
    assert earlier["kpis"]["forecast_status"]["state"] == "not_current_month"
    future = _summary(client, user, auth_header, period_start="2026-09-01", period_end="2026-09-23")
    assert future["kpis"]["forecast_status"]["state"] == "not_current_month" and future["kpis"]["forecast_month_end"] == []

