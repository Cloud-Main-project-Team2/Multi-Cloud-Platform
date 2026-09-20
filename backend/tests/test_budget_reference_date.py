"""PR 8 — 예산 기준일(reference_date)과 결측 검사 범위 분리(app/cost/budget.py · D1).

기존 호출(기준일 생략 = 오늘)의 동작이 그대로인지, 과거 기준일이 그 날을 결측 검사에 포함하는지,
월초에 이전 달 예산을 대신 가져오지 않는지, 미래 기준일이 오늘로 내려오는지 확인한다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.cost.budget import REASON_MISSING_DAYS, REASON_NO_COMPLETED_DAYS, compute_budget_status
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Team, TeamBudget

D = dt.timedelta


def _setup(db, user, *, budget_start=dt.date(2026, 8, 1)):
    a = CloudAccount(user_id=user.id, provider="aws", external_account_id="1")
    db.add(a)
    db.flush()
    t = Team(user_id=user.id, name="팀", currency="USD")
    db.add(t)
    db.flush()
    a.team_id = t.id
    db.add(TeamBudget(team_id=t.id, period_type="monthly", start_date=budget_start, limit_amount=Decimal("300"), currency="USD"))
    db.flush()
    return a, t


def _cover(db, a, start, end):
    now = dt.datetime.now(dt.timezone.utc)
    db.add(CostIngestionRun(user_id=a.user_id, cloud_account_id=a.id, trigger_type="auto", status="success",
                            period_start=start, period_end=end, requested_at=now, finished_at=now))
    db.flush()


def _cost(db, a, day, amount):
    db.add(CloudAccountCost(cloud_account_id=a.id, provider="aws", charge_category="usage", service="AmazonEC2",
                            amount=Decimal(amount), currency="USD", period_start=day, period_end=day + D(days=1),
                            as_of=dt.datetime.now(dt.timezone.utc), source="aws_cost_explorer", source_record_key=f"{a.id}:{day}:{amount}"))
    db.flush()


def test_past_reference_date_recomputes_that_month(db_session, make_user):
    a, t = _setup(db_session, make_user())
    _cover(db_session, a, dt.date(2026, 8, 1), dt.date(2026, 9, 1))
    _cost(db_session, a, dt.date(2026, 8, 10), "150")
    _cost(db_session, a, dt.date(2026, 9, 10), "999")  # 9월 것은 안 섞인다
    s = compute_budget_status(db_session, t, today=dt.date(2026, 9, 20), reference_date=dt.date(2026, 8, 31))
    assert s["computable"] is True and s["ratio_pct"] == "50.0"
    assert s["budget"]["period_start"] == dt.date(2026, 8, 1) and s["budget"]["period_end"] == dt.date(2026, 9, 1)
    assert s["budget"]["basis_date"] == dt.date(2026, 8, 31) and s["budget"]["period_state"] == "in_progress"
    assert s["forecast"] is None  # 과거 재계산엔 전망 없음


def test_past_last_day_uncollected_is_missing_days(db_session, make_user):
    """기준일(8/31) 자체가 미수집이면 MISSING_DAYS — 오늘이 아니라 완료된 날이므로 검사에 포함된다."""
    a, t = _setup(db_session, make_user())
    _cover(db_session, a, dt.date(2026, 8, 1), dt.date(2026, 8, 31))  # 8/31 빠짐
    s = compute_budget_status(db_session, t, today=dt.date(2026, 9, 20), reference_date=dt.date(2026, 8, 31))
    assert s["computable"] is False and s["reason_code"] == REASON_MISSING_DAYS


def test_default_call_keeps_today_excluded_from_missing_check(db_session, make_user):
    """기준일 생략(=오늘): 오늘은 아직 수집될 수 없으므로 어제까지만 검사 — PR 7 동작 그대로."""
    today = dt.date(2026, 9, 20)
    a, t = _setup(db_session, make_user(), budget_start=dt.date(2026, 9, 1))
    _cover(db_session, a, dt.date(2026, 9, 1), today)  # 오늘은 미수집
    s = compute_budget_status(db_session, t, today=today)
    assert s["computable"] is True and s["budget"]["basis_date"] == today


def test_first_day_of_month_has_no_completed_days(db_session, make_user):
    """월초 1일 조회: 구간은 시작됐지만 완료된 날이 없다 → 이전 달 예산을 대신 보여주지 않고 산출 불가."""
    a, t = _setup(db_session, make_user())
    _cover(db_session, a, dt.date(2026, 8, 1), dt.date(2026, 9, 1))
    _cost(db_session, a, dt.date(2026, 8, 10), "150")
    s = compute_budget_status(db_session, t, today=dt.date(2026, 9, 1))
    assert s["computable"] is False and s["reason_code"] == REASON_NO_COMPLETED_DAYS
    assert s["budget"]["period_start"] == dt.date(2026, 9, 1)  # 9월 구간이지 8월이 아니다
    assert s["ratio_pct"] is None


def test_future_reference_date_is_clamped_to_today(db_session, make_user):
    today = dt.date(2026, 9, 20)
    a, t = _setup(db_session, make_user(), budget_start=dt.date(2026, 9, 1))
    _cover(db_session, a, dt.date(2026, 9, 1), today)
    s_default = compute_budget_status(db_session, t, today=today)
    s_future = compute_budget_status(db_session, t, today=today, reference_date=dt.date(2026, 12, 31))
    assert s_future["budget"]["basis_date"] == today
    assert s_future["computable"] == s_default["computable"] and s_future["ratio_pct"] == s_default["ratio_pct"]


def test_budget_status_api_accepts_reference_date(client, make_user, auth_header, db_session):
    user = make_user()
    a, t = _setup(db_session, user)
    _cover(db_session, a, dt.date(2026, 8, 1), dt.date(2026, 9, 1))
    _cost(db_session, a, dt.date(2026, 8, 10), "60")
    r = client.get(f"/api/v1/teams/{t.id}/budget-status?reference_date=2026-08-31", headers=auth_header(user))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["budget"]["basis_date"] == "2026-08-31" and d["ratio_pct"] == "20.0"
    assert client.get(f"/api/v1/teams/{t.id}/budget-status?reference_date=nope", headers=auth_header(user)).status_code == 422
