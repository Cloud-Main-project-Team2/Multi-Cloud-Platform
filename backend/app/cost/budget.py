"""팀 예산 — 적용 기간·상태·소진율 계산(PR 7, docs/비용_개발문서/05_API계약.md §6-2 · 08 §6).

**DB만 읽는다**(ADR-040) — CSP import 금지. 예산 초과 *차단*(budget_gate)은 ADR-042로 보류됐고
이 모듈은 판정만 한다.

규칙(2026-09-19 승현 확정):
- 반복 예산(`end_date IS NULL`)은 period_type과 무관하게 **팀당 하나의 시간순 체인**이다. 각 행의
  효력은 자기 `start_date`부터 다음 반복 행의 `start_date` 전까지. 계산 구간은
  `period_start = max(달력 시작, 행 start_date)`, `period_end = min(달력 종료, 다음 행 start_date)`.
- custom은 해당 기간의 임시 override — budget-status는 진행 중 custom → 진행 중 반복 → 가장
  가까운 예정 → 가장 최근 종료 → NO_BUDGET 순으로 고른다.
- 예산 미설정은 $0이 아니다. `computable=false`면 `ratio_pct=null`. 100을 넘는 값은 자르지 않는다.
- **기준일(reference_date)과 오늘(today)을 분리한다**(2026-09-19, PR 8): 기준일은 "어느 예산·어느
  구간을 보는가"를 정하고, 사용액은 기준일까지(포함) 합하며, 결측 검사는 `min(기준일+1, 오늘)`까지 —
  기준일이 과거면 그 날도 완료된 날이라 결측 검사에 **포함**한다(오늘만 "아직 수집될 수 없음"으로
  제외). 기본값(기준일=오늘)일 때 동작은 이전과 같다. 기준일이 속한 구간에 완료된 날이 하나도 없으면
  이전 구간의 예산을 대신 가져오지 않고 `NO_COMPLETED_DAYS`로 산출 불가를 돌려준다. 이것은 "당시
  저장된 스냅샷"이 아니라 **지금 저장된 데이터로 그 기간을 다시 계산한 것**이다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.cost import is_cost_supported
from app.cost.coverage import missing_days as coverage_missing_days
from app.cost.query import accounts_currency_map, money, staleness_threshold_hours
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Team, TeamBudget, TeamBudgetNotification

THRESHOLDS = (80, 100)
USAGE_BASIS = "usage_before_credits"  # 확정 5
USAGE_CATEGORIES = ["usage"]

REASON_MISSING_DAYS = "MISSING_DAYS"
REASON_CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
REASON_NO_BUDGET = "NO_BUDGET"
REASON_NO_ACCOUNTS = "NO_ACCOUNTS"
REASON_UNSUPPORTED = "UNSUPPORTED"
REASON_NO_COMPLETED_DAYS = "NO_COMPLETED_DAYS"  # 기준일 구간에 완료된 날이 없다(PR 8 추가 — 05 §6-2 5종 밖)

STATE_UPCOMING = "upcoming"
STATE_IN_PROGRESS = "in_progress"
STATE_ENDED = "ended"


# --- 달력 구간 ------------------------------------------------------------------------------


def _add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    return dt.date(d.year + y, m + 1, 1)


def calendar_period(period_type: str, on: dt.date) -> tuple[dt.date, dt.date]:
    """`on`이 속한 달력 구간 [start, end). 분기는 달력 기준(1/1·4/1·7/1·10/1)이다(확정 8)."""
    if period_type == "monthly":
        start = on.replace(day=1)
        return start, _add_months(start, 1)
    if period_type == "quarterly":
        q_month = ((on.month - 1) // 3) * 3 + 1
        start = dt.date(on.year, q_month, 1)
        return start, _add_months(start, 3)
    if period_type == "annual":
        start = dt.date(on.year, 1, 1)
        return start, dt.date(on.year + 1, 1, 1)
    raise ValueError(f"달력 구간이 없는 period_type: {period_type}")


# --- 체인·상태 ------------------------------------------------------------------------------


def team_budgets(db: Session, team_id: int) -> list[TeamBudget]:
    return (
        db.query(TeamBudget)
        .filter(TeamBudget.team_id == team_id)
        .order_by(TeamBudget.start_date, TeamBudget.id)
        .all()
    )


def recurring_chain(budgets: list[TeamBudget]) -> list[TeamBudget]:
    return sorted((b for b in budgets if b.period_type != "custom"), key=lambda b: (b.start_date, b.id))


def effective_end(budget: TeamBudget, chain: list[TeamBudget]) -> dt.date | None:
    """반복 행의 효력 종료(제외 경계) = 다음 반복 행의 start_date. 마지막 행이면 None(무기한)."""
    if budget.period_type == "custom":
        return budget.end_date
    for b in chain:
        if b.start_date > budget.start_date or (b.start_date == budget.start_date and b.id > budget.id):
            return b.start_date
    return None


def budget_state(budget: TeamBudget, chain: list[TeamBudget], today: dt.date) -> str:
    end = effective_end(budget, chain)
    if today < budget.start_date:
        return STATE_UPCOMING
    if end is not None and today >= end:
        return STATE_ENDED
    return STATE_IN_PROGRESS


def budget_period(budget: TeamBudget, chain: list[TeamBudget], today: dt.date) -> tuple[dt.date, dt.date]:
    """지금 보여줄 계산 구간 [period_start, period_end). custom은 자기 기간 그대로. 반복은 상태에
    따라 기준일(진행 중=오늘 · 예정=시작일 · 종료=효력 마지막 날)이 속한 달력 구간을 효력 범위로
    자른다."""
    if budget.period_type == "custom":
        return budget.start_date, budget.end_date
    end = effective_end(budget, chain)
    state = budget_state(budget, chain, today)
    if state == STATE_UPCOMING:
        ref = budget.start_date
    elif state == STATE_ENDED:
        ref = end - dt.timedelta(days=1)
    else:
        ref = today
    cal_start, cal_end = calendar_period(budget.period_type, ref)
    period_start = max(cal_start, budget.start_date)
    period_end = min(cal_end, end) if end is not None else cal_end
    return period_start, period_end


def select_status_budget(budgets: list[TeamBudget], today: dt.date) -> TeamBudget | None:
    """budget-status가 보여줄 예산 하나. 진행 중 custom → 진행 중 반복 → 가장 가까운 예정 →
    가장 최근 종료 → 없음."""
    chain = recurring_chain(budgets)
    states = {b.id: budget_state(b, chain, today) for b in budgets}
    in_progress_custom = [b for b in budgets if b.period_type == "custom" and states[b.id] == STATE_IN_PROGRESS]
    if in_progress_custom:
        return max(in_progress_custom, key=lambda b: (b.start_date, b.id))
    in_progress = [b for b in budgets if states[b.id] == STATE_IN_PROGRESS]
    if in_progress:
        return max(in_progress, key=lambda b: (b.start_date, b.id))
    upcoming = [b for b in budgets if states[b.id] == STATE_UPCOMING]
    if upcoming:
        return min(upcoming, key=lambda b: (b.start_date, b.id))
    ended = [b for b in budgets if states[b.id] == STATE_ENDED]
    if ended:
        return max(ended, key=lambda b: (budget_period(b, chain, today)[1], b.id))
    return None


def find_active_recurring(budgets: list[TeamBudget]) -> TeamBudget | None:
    """체인의 마지막(가장 늦은 start_date) 반복 행 — 새 반복 행은 이보다 뒤에서 시작해야 한다."""
    chain = recurring_chain(budgets)
    return chain[-1] if chain else None


def find_custom_overlap(
    budgets: list[TeamBudget], start: dt.date, end: dt.date, *, exclude_id: int | None = None
) -> TeamBudget | None:
    for b in budgets:
        if b.period_type != "custom" or b.id == exclude_id:
            continue
        if b.start_date < end and start < b.end_date:
            return b
    return None


# --- 소진율 ---------------------------------------------------------------------------------


def _missing_days_for_account(
    db: Session, account: CloudAccount, period_start: dt.date, check_end: dt.date
) -> list[dt.date]:
    """계정 하나의 결측일 — 정의는 coverage.py 한 곳(행 또는 성공 run 범위)이다. `check_end`는
    호출부가 정한다(기준일+1과 오늘 중 이른 쪽)."""
    if check_end <= period_start:
        return []
    return coverage_missing_days(db, account.id, period_start, check_end)


def _sum_usage(
    db: Session, account_ids: list[int], currency: str, period_start: dt.date, period_end: dt.date,
    *, categories: list[str] | None,
) -> tuple[Decimal | None, dt.datetime | None, bool]:
    if not account_ids:
        return None, None, False
    query = db.query(
        func.sum(CloudAccountCost.amount), func.max(CloudAccountCost.as_of), func.bool_or(CloudAccountCost.is_estimated)
    ).filter(
        CloudAccountCost.cloud_account_id.in_(account_ids),
        CloudAccountCost.currency == currency,
        CloudAccountCost.period_start >= period_start,
        CloudAccountCost.period_start < period_end,
    )
    if categories:
        query = query.filter(CloudAccountCost.charge_category.in_(categories))
    total, as_of, est = query.one()
    return total, as_of, bool(est)


def _ratio_pct(amount: Decimal, limit: Decimal) -> str:
    return str((amount / limit * Decimal(100)).quantize(Decimal("0.1")))


def compute_budget_status(
    db: Session, team: Team, today: dt.date | None = None, *, reference_date: dt.date | None = None
) -> dict:
    """`GET /teams/{id}/budget-status` 본체이자 임계 알림(notify.py)의 판정 근거. 반환 dict는
    `schemas.teams.BudgetStatusData` 모양(날짜는 date/datetime 그대로 — 라우터가 문자열로 바꾼다).

    `today`는 실제 오늘(수집될 수 없는 날의 경계), `reference_date`는 보고 싶은 기준일. 생략하면 둘 다
    오늘이라 기존 호출(화면·알림)의 동작이 그대로다. 기준일이 오늘보다 뒤면 오늘로 내린다 — 미래
    날짜를 기준일로 쓰지 않는다."""
    today = today or dt.date.today()
    reference_date = min(reference_date or today, today)
    out: dict = {
        "team_id": team.id,
        "budget": None,
        "usage": None,
        "ratio_pct": None,
        "forecast": None,
        "thresholds": [{"percent": p, "crossed": False, "crossed_at": None, "notified": False} for p in THRESHOLDS],
        "excluded_accounts": [],
        "computable": False,
        "reason_code": None,
        "staleness_threshold_hours": staleness_threshold_hours(),
    }

    budgets = team_budgets(db, team.id)
    budget = select_status_budget(budgets, reference_date)
    if budget is None:
        out["reason_code"] = REASON_NO_BUDGET
        return out

    chain = recurring_chain(budgets)
    state = budget_state(budget, chain, reference_date)
    period_start, period_end = budget_period(budget, chain, reference_date)
    out["budget"] = {
        "id": budget.id, "period_type": budget.period_type, "limit_amount": money(budget.limit_amount),
        "currency": budget.currency, "period_start": period_start, "period_end": period_end, "period_state": state,
        "basis_date": reference_date,
    }
    # 사용액은 기준일까지(포함), 결측 검사는 기준일까지(포함)이되 실제 오늘은 제외 — 오늘은 아직
    # 수집될 수 없다. 기준일이 과거면 두 범위가 같다.
    usage_end = min(period_end, reference_date + dt.timedelta(days=1))
    check_end = min(usage_end, today)

    notified_rows = (
        db.query(TeamBudgetNotification)
        .filter(TeamBudgetNotification.team_budget_id == budget.id, TeamBudgetNotification.period_start == period_start)
        .all()
    )
    notified = {row.threshold: row for row in notified_rows}

    accounts = (
        db.query(CloudAccount).filter(CloudAccount.team_id == team.id).order_by(CloudAccount.id).all()
    )
    if not accounts:
        out["reason_code"] = REASON_NO_ACCOUNTS
        return out

    supported = [a for a in accounts if is_cost_supported(a.provider)]
    currency_of = accounts_currency_map(db, accounts)
    excluded = []
    for a in accounts:
        if not is_cost_supported(a.provider):
            excluded.append(_excluded(a, currency_of.get(a.id), "unsupported"))
        elif currency_of.get(a.id) is not None and currency_of[a.id] != team.currency:
            excluded.append(_excluded(a, currency_of[a.id], "currency_mismatch"))
    out["excluded_accounts"] = excluded

    if not supported:
        out["reason_code"] = REASON_UNSUPPORTED
        return out

    included = [a for a in supported if currency_of.get(a.id) in (None, team.currency)]
    included_ids = [a.id for a in included]

    # 사용액은 팀 통화로만 합한다 — 다른 통화 계정은 위에서 excluded로 뺐다(ADR-020).
    amount, as_of, is_est = _sum_usage(db, included_ids, team.currency, period_start, usage_end, categories=USAGE_CATEGORIES)
    net_amount, _, _ = _sum_usage(db, included_ids, team.currency, period_start, usage_end, categories=None)
    amount = amount if amount is not None else Decimal("0")
    net_amount = net_amount if net_amount is not None else Decimal("0")
    out["usage"] = {
        "currency": team.currency, "amount": money(amount), "basis": USAGE_BASIS,
        "net_amount": money(net_amount), "as_of": as_of, "is_estimated": is_est,
    }

    currency_mismatch = budget.currency != team.currency or any(e["reason"] == "currency_mismatch" for e in excluded)
    if currency_mismatch:
        out["reason_code"] = REASON_CURRENCY_MISMATCH
        return out

    if state != STATE_UPCOMING:
        if check_end <= period_start:
            # 구간은 시작됐지만 완료된 날이 아직 없다(월초 1일 조회 등) — 0%라고 말하지 않고
            # 이전 구간의 예산을 대신 보여주지도 않는다.
            out["reason_code"] = REASON_NO_COMPLETED_DAYS
            return out
        for a in included:
            if _missing_days_for_account(db, a, period_start, check_end):
                out["reason_code"] = REASON_MISSING_DAYS
                return out

    limit = Decimal(budget.limit_amount)
    ratio = _ratio_pct(amount, limit)
    out["computable"] = True
    out["ratio_pct"] = ratio
    ratio_dec = Decimal(ratio)
    out["thresholds"] = [
        {
            "percent": p,
            "crossed": ratio_dec >= p,
            "crossed_at": notified[p].notified_at if p in notified else None,
            "notified": p in notified,
        }
        for p in THRESHOLDS
    ]

    if state == STATE_IN_PROGRESS and reference_date == today:
        # 전망은 진행 중 + 기준일이 오늘일 때만(확정 8) — 어제까지의 실측을 남은 일수로 늘린다
        # (CF-003과 같은 방법). 과거 기준일 재계산에는 전망이 의미 없다.
        based_through = min(today, period_end) - dt.timedelta(days=1)
        elapsed = (based_through - period_start).days + 1
        total_days = (period_end - period_start).days
        if elapsed > 0 and total_days > 0:
            through_yesterday, _, _ = _sum_usage(
                db, included_ids, team.currency, period_start, based_through + dt.timedelta(days=1), categories=USAGE_CATEGORIES
            )
            through_yesterday = through_yesterday if through_yesterday is not None else Decimal("0")
            forecast = (through_yesterday / Decimal(elapsed) * Decimal(total_days)).quantize(Decimal("0.000001"))
            out["forecast"] = {
                "amount": money(forecast), "ratio_pct": _ratio_pct(forecast, limit), "based_through": based_through,
            }
    return out


def _excluded(account: CloudAccount, currency: str | None, reason: str) -> dict:
    return {
        "cloud_account_id": account.id, "provider": account.provider,
        "account_label": account.account_label, "currency": currency, "reason": reason,
    }
