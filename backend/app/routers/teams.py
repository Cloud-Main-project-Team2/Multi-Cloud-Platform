"""팀·예산 API 9종(PR 7 — docs/비용_개발문서/05_API계약.md §6, 09 §10).

팀: GET/POST /teams · PATCH/DELETE /teams/{id} · PUT /teams/{id}/accounts
예산: GET/POST /teams/{id}/budgets · PATCH/DELETE /team-budgets/{id} · GET /teams/{id}/budget-status

- 팀은 한 사용자가 연결한 계정의 묶음이다. RBAC가 아니다 — 소유권은 전부 `teams.user_id`.
- 예산 초과 *차단*은 ADR-042로 보류 — 이 파일은 `routers/provisioning.py`를 건드리지 않고
  `BUDGET_EXCEEDED`도 던지지 않는다. 예산을 보고 알림을 받는 것까지만 한다.
- 감사 기록은 만들지 않는다(명세 §14 승인 전) — created_at/updated_at만 남는다.
- 오류 코드: `TEAM_NOT_FOUND`·`TEAM_BUDGET_NOT_FOUND`만 신규 채택(2026-09-19). 1계정 1팀 위반·
  custom 겹침·활성 반복 중복은 `409 CONFLICT` + details로, custom 1년 초과는 `422 VALIDATION_ERROR`로
  흡수한다(05 §2-5의 미채택 코드를 쓰지 않는다).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.cost.budget import (
    STATE_UPCOMING,
    budget_period,
    budget_state,
    compute_budget_status,
    find_active_recurring,
    find_custom_overlap,
    recurring_chain,
    select_status_budget,
    team_budgets,
)
from app.cost.notify import evaluate_budget_thresholds
from app.cost.query import accounts_currency_map, money
from app.db import get_db
from app.deps import get_current_user, require_confirmation
from app.errors import ApiError, validation_error
from app.logging_config import log_business_event
from app.models import CloudAccount, CloudAccountCost, Team, TeamBudget, User
from app.schemas.teams import (
    ActiveBudgetBrief,
    BudgetCreateRequest,
    BudgetDetailResponse,
    BudgetListData,
    BudgetListResponse,
    BudgetOut,
    BudgetPatchRequest,
    BudgetStatusBudget,
    BudgetStatusData,
    BudgetStatusExcludedAccount,
    BudgetStatusForecast,
    BudgetStatusResponse,
    BudgetStatusThreshold,
    BudgetStatusUsage,
    TeamAccountOut,
    TeamAccountsPutRequest,
    TeamCreateRequest,
    TeamDetailResponse,
    TeamListData,
    TeamListResponse,
    TeamOut,
    TeamPatchRequest,
    UnassignedOut,
)
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["teams"])

MAX_CUSTOM_DAYS = 366  # DB CHECK(start + 1 year)와 같은 규칙 — 윤년 포함 1년


# --- 공통 -----------------------------------------------------------------------------------


def _parse_id(value: str, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise validation_error(f"{field}는 숫자 ID여야 합니다.", details=[{"field": field, "reason": "invalid"}]) from exc


def _get_owned_team(db: Session, user: User, team_id: str) -> Team:
    try:
        tid = int(team_id)
    except (TypeError, ValueError):
        raise ApiError(404, "TEAM_NOT_FOUND", "팀을 찾을 수 없습니다.")
    team = db.query(Team).filter(Team.id == tid, Team.user_id == user.id).first()
    if team is None:
        raise ApiError(404, "TEAM_NOT_FOUND", "팀을 찾을 수 없습니다.")
    return team


def _get_owned_budget(db: Session, user: User, budget_id: str) -> tuple[TeamBudget, Team]:
    try:
        bid = int(budget_id)
    except (TypeError, ValueError):
        raise ApiError(404, "TEAM_BUDGET_NOT_FOUND", "예산을 찾을 수 없습니다.")
    row = (
        db.query(TeamBudget, Team)
        .join(Team, Team.id == TeamBudget.team_id)
        .filter(TeamBudget.id == bid, Team.user_id == user.id)
        .first()
    )
    if row is None:
        raise ApiError(404, "TEAM_BUDGET_NOT_FOUND", "예산을 찾을 수 없습니다.")
    return row[0], row[1]


def _team_accounts(db: Session, team_id: int) -> list[CloudAccount]:
    return db.query(CloudAccount).filter(CloudAccount.team_id == team_id).order_by(CloudAccount.id).all()


def _account_out(account: CloudAccount, currency: str | None, team_currency: str | None) -> TeamAccountOut:
    return TeamAccountOut(
        cloud_account_id=str_id(account.id), provider=account.provider,
        external_account_id=account.external_account_id, account_label=account.account_label,
        currency=currency,
        currency_matches_team=None if (currency is None or team_currency is None) else currency == team_currency,
    )


def _serialize_team(db: Session, team: Team, today: dt.date | None = None) -> TeamOut:
    today = today or dt.date.today()
    accounts = _team_accounts(db, team.id)
    currency_of = accounts_currency_map(db, accounts)
    budgets = team_budgets(db, team.id)
    chain = recurring_chain(budgets)
    active = select_status_budget(budgets, today)
    active_out = None
    if active is not None and budget_state(active, chain, today) == "in_progress":
        active_out = ActiveBudgetBrief(
            id=str_id(active.id), period_type=active.period_type, limit_amount=money(active.limit_amount),
            currency=active.currency, start_date=active.start_date.isoformat(),
            end_date=active.end_date.isoformat() if active.end_date else None,
        )
    return TeamOut(
        id=str_id(team.id), name=team.name, currency=team.currency, account_count=len(accounts),
        accounts=[_account_out(a, currency_of.get(a.id), team.currency) for a in accounts],
        active_budget=active_out, created_at=iso_z(team.created_at), updated_at=iso_z(team.updated_at),
    )


def _serialize_budget(budget: TeamBudget, chain: list[TeamBudget], today: dt.date) -> BudgetOut:
    return BudgetOut(
        id=str_id(budget.id), team_id=str_id(budget.team_id), period_type=budget.period_type,
        start_date=budget.start_date.isoformat(), end_date=budget.end_date.isoformat() if budget.end_date else None,
        limit_amount=money(budget.limit_amount), currency=budget.currency,
        state=budget_state(budget, chain, today), created_at=iso_z(budget.created_at), updated_at=iso_z(budget.updated_at),
    )


def _duplicate_name(db: Session, user: User, name: str, *, exclude_id: int | None = None) -> bool:
    query = db.query(Team).filter(Team.user_id == user.id, Team.name == name)
    if exclude_id is not None:
        query = query.filter(Team.id != exclude_id)
    return query.first() is not None


def _validate_custom_window(start: dt.date, end: dt.date | None) -> dt.date:
    if end is None:
        raise validation_error("custom 예산은 end_date가 필요합니다.", details=[{"field": "end_date", "reason": "required"}])
    if end <= start:
        raise validation_error("end_date는 start_date보다 커야 합니다.", details=[{"field": "end_date", "reason": "invalid_range"}])
    if (end - start).days > MAX_CUSTOM_DAYS:
        # 05 §2-5의 BUDGET_PERIOD_TOO_LONG은 미채택 — VALIDATION_ERROR로 흡수한다.
        raise validation_error("custom 예산 기간은 최대 1년입니다.", details=[{"field": "end_date", "reason": "range_too_long"}])
    return end


def _safe_evaluate(db: Session, team: Team) -> None:
    """쓰기 작업 뒤 임계 알림 평가 — 판정 실패가 방금 한 쓰기를 되돌리면 안 되므로 예외는 로그만."""
    try:
        evaluate_budget_thresholds(db, team)
    except Exception:  # noqa: BLE001
        log_business_event("cost.budget_threshold.evaluate_failed", level="ERROR", team_id=team.id, exc_info=True)


# --- 팀 -----------------------------------------------------------------------------------


@router.get("/teams", response_model=TeamListResponse)
def list_teams(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamListResponse:
    teams = db.query(Team).filter(Team.user_id == current_user.id).order_by(Team.id).all()
    items = [_serialize_team(db, t) for t in teams]

    unassigned_accounts = (
        db.query(CloudAccount)
        .filter(CloudAccount.user_id == current_user.id, CloudAccount.team_id.is_(None))
        .order_by(CloudAccount.id)
        .all()
    )
    currency_of = accounts_currency_map(db, unassigned_accounts)
    unassigned = UnassignedOut(
        account_count=len(unassigned_accounts),
        accounts=[_account_out(a, currency_of.get(a.id), None) for a in unassigned_accounts],
    )
    return TeamListResponse(data=TeamListData(items=items, unassigned=unassigned, total=len(items)))


@router.post("/teams", status_code=201, response_model=TeamDetailResponse)
def create_team(
    payload: TeamCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamDetailResponse:
    if _duplicate_name(db, current_user, payload.name):
        raise ApiError(409, "CONFLICT", "같은 이름의 팀이 이미 있습니다.", details=[{"field": "name", "reason": "duplicate"}])
    team = Team(user_id=current_user.id, name=payload.name, currency=payload.currency)
    db.add(team)
    db.commit()
    db.refresh(team)
    return TeamDetailResponse(data=_serialize_team(db, team))


@router.patch("/teams/{team_id}", response_model=TeamDetailResponse)
def patch_team(
    team_id: str,
    payload: TeamPatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamDetailResponse:
    team = _get_owned_team(db, current_user, team_id)
    if payload.name is not None and payload.name != team.name:
        if _duplicate_name(db, current_user, payload.name, exclude_id=team.id):
            raise ApiError(409, "CONFLICT", "같은 이름의 팀이 이미 있습니다.", details=[{"field": "name", "reason": "duplicate"}])
        team.name = payload.name
    currency_changed = payload.currency is not None and payload.currency != team.currency
    if currency_changed:
        # 이미 수집된 금액이 있는 팀의 통화를 바꾸면 과거 사용률이 전부 거짓이 된다(ADR-020).
        has_costs = (
            db.query(CloudAccountCost.id)
            .join(CloudAccount, CloudAccount.id == CloudAccountCost.cloud_account_id)
            .filter(CloudAccount.team_id == team.id)
            .first()
            is not None
        )
        if has_costs:
            raise ApiError(
                409, "CONFLICT", "수집된 비용이 있는 팀의 통화는 변경할 수 없습니다.",
                details=[{"field": "currency", "reason": "team_has_collected_costs"}],
            )
        team.currency = payload.currency
    db.flush()
    if currency_changed:
        _safe_evaluate(db, team)
    db.commit()
    db.refresh(team)
    return TeamDetailResponse(data=_serialize_team(db, team))


@router.delete("/teams/{team_id}", status_code=204)
def delete_team(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> Response:
    team = _get_owned_team(db, current_user, team_id)
    # 소속 계정은 FK ON DELETE SET NULL로 미배정이 되고, 예산·알림 중복 방지 기록은 CASCADE로
    # 지워진다. cloud_account_costs는 팀을 참조하지 않으므로 과거 비용은 그대로 남는다(05 §6-1).
    db.delete(team)
    db.commit()
    db.expire_all()
    return Response(status_code=204)


@router.put("/teams/{team_id}/accounts", response_model=TeamDetailResponse)
def put_team_accounts(
    team_id: str,
    payload: TeamAccountsPutRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamDetailResponse:
    team = _get_owned_team(db, current_user, team_id)
    wanted_ids = sorted({_parse_id(v, "cloud_account_ids") for v in payload.cloud_account_ids})

    accounts = []
    if wanted_ids:
        accounts = (
            db.query(CloudAccount)
            .filter(CloudAccount.user_id == current_user.id, CloudAccount.id.in_(wanted_ids))
            .all()
        )
        found = {a.id for a in accounts}
        missing = [i for i in wanted_ids if i not in found]
        if missing:
            raise ApiError(
                404, "CLOUD_ACCOUNT_NOT_FOUND", "클라우드 계정을 찾을 수 없습니다.",
                details=[{"field": "cloud_account_ids", "cloud_account_id": str(i), "reason": "not_found"} for i in missing],
            )
        # 1계정 1팀(확정 7). ACCOUNT_ALREADY_IN_TEAM은 미채택 코드 — CONFLICT + details로 흡수한다.
        taken = [a for a in accounts if a.team_id is not None and a.team_id != team.id]
        if taken:
            raise ApiError(
                409, "CONFLICT", "이미 다른 팀에 속한 계정이 있습니다.",
                details=[
                    {"field": "cloud_account_ids", "cloud_account_id": str(a.id), "team_id": str(a.team_id), "reason": "account_already_in_team"}
                    for a in taken
                ],
            )

    # 목록에서 빠진 현재 소속 계정은 미배정으로 돌아간다.
    for a in _team_accounts(db, team.id):
        if a.id not in wanted_ids:
            a.team_id = None
    for a in accounts:
        a.team_id = team.id
    db.flush()
    _safe_evaluate(db, team)
    db.commit()
    db.refresh(team)
    return TeamDetailResponse(data=_serialize_team(db, team))


# --- 예산 -----------------------------------------------------------------------------------


@router.get("/teams/{team_id}/budgets", response_model=BudgetListResponse)
def list_budgets(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BudgetListResponse:
    team = _get_owned_team(db, current_user, team_id)
    today = dt.date.today()
    budgets = team_budgets(db, team.id)
    chain = recurring_chain(budgets)
    items = [_serialize_budget(b, chain, today) for b in sorted(budgets, key=lambda b: (b.start_date, b.id), reverse=True)]
    return BudgetListResponse(data=BudgetListData(items=items, total=len(items)))


@router.post("/teams/{team_id}/budgets", status_code=201, response_model=BudgetDetailResponse)
def create_budget(
    team_id: str,
    payload: BudgetCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BudgetDetailResponse:
    team = _get_owned_team(db, current_user, team_id)
    budgets = team_budgets(db, team.id)
    end_date: dt.date | None = None

    if payload.period_type == "custom":
        end_date = _validate_custom_window(payload.start_date, payload.end_date)
        overlap = find_custom_overlap(budgets, payload.start_date, end_date)
        if overlap is not None:
            # BUDGET_PERIOD_OVERLAP은 미채택 — CONFLICT로 흡수한다.
            raise ApiError(
                409, "CONFLICT", "기간이 겹치는 custom 예산이 이미 있습니다.",
                details=[{"field": "start_date", "reason": "custom_period_overlap", "existing_budget_id": str(overlap.id)}],
            )
    else:
        if payload.end_date is not None:
            raise validation_error("반복 예산은 end_date를 받지 않습니다.", details=[{"field": "end_date", "reason": "not_allowed"}])
        # 반복 예산은 팀당 시간순 체인 하나 — 새 행은 기존 최신 행보다 뒤에서 시작해야 한다(=한도
        # 변경은 새 행, 이력 보존). 같거나 이르면 활성 반복 주기 중복이다.
        latest = find_active_recurring(budgets)
        if latest is not None and payload.start_date <= latest.start_date:
            raise ApiError(
                409, "CONFLICT", "이미 활성 반복 예산이 있습니다. 더 늦은 적용 시작일로 새 예산을 만드세요.",
                details=[{"field": "start_date", "reason": "active_recurring_exists", "existing_budget_id": str(latest.id)}],
            )

    budget = TeamBudget(
        team_id=team.id, period_type=payload.period_type, start_date=payload.start_date, end_date=end_date,
        limit_amount=payload.limit_amount, currency=payload.currency or team.currency,
    )
    db.add(budget)
    db.flush()
    _safe_evaluate(db, team)
    db.commit()
    db.refresh(budget)
    chain = recurring_chain(team_budgets(db, team.id))
    return BudgetDetailResponse(data=_serialize_budget(budget, chain, dt.date.today()))


@router.patch("/team-budgets/{budget_id}", response_model=BudgetDetailResponse)
def patch_budget(
    budget_id: str,
    payload: BudgetPatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BudgetDetailResponse:
    budget, team = _get_owned_budget(db, current_user, budget_id)
    locked = [k for k in ("start_date", "period_type", "currency") if k in (payload.model_extra or {})]
    if locked:
        raise ApiError(
            409, "CONFLICT", "적용 시작일·주기·통화는 바꿀 수 없습니다. 새 예산을 만드세요.",
            details=[{"field": k, "reason": "create_new_budget"} for k in locked],
        )
    today = dt.date.today()
    budgets = team_budgets(db, team.id)
    chain = recurring_chain(budgets)
    if budget_state(budget, chain, today) != STATE_UPCOMING:
        raise ApiError(
            409, "CONFLICT", "이미 시작된 예산은 수정할 수 없습니다. 한도를 바꾸려면 새 예산을 만드세요.",
            details=[{"field": "limit_amount", "reason": "budget_already_started"}],
        )
    if payload.end_date is not None:
        if budget.period_type != "custom":
            raise validation_error("반복 예산은 end_date를 받지 않습니다.", details=[{"field": "end_date", "reason": "not_allowed"}])
        end_date = _validate_custom_window(budget.start_date, payload.end_date)
        overlap = find_custom_overlap(budgets, budget.start_date, end_date, exclude_id=budget.id)
        if overlap is not None:
            raise ApiError(
                409, "CONFLICT", "기간이 겹치는 custom 예산이 이미 있습니다.",
                details=[{"field": "end_date", "reason": "custom_period_overlap", "existing_budget_id": str(overlap.id)}],
            )
        budget.end_date = end_date
    if payload.limit_amount is not None:
        budget.limit_amount = payload.limit_amount
    db.flush()
    db.commit()
    db.refresh(budget)
    chain = recurring_chain(team_budgets(db, team.id))
    return BudgetDetailResponse(data=_serialize_budget(budget, chain, today))


@router.delete("/team-budgets/{budget_id}", status_code=204)
def delete_budget(
    budget_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> Response:
    budget, team = _get_owned_budget(db, current_user, budget_id)
    db.delete(budget)
    db.flush()
    _safe_evaluate(db, team)
    db.commit()
    return Response(status_code=204)


@router.get("/teams/{team_id}/budget-status", response_model=BudgetStatusResponse)
def get_budget_status(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BudgetStatusResponse:
    team = _get_owned_team(db, current_user, team_id)
    s = compute_budget_status(db, team)
    b = s["budget"]
    u = s["usage"]
    f = s["forecast"]
    return BudgetStatusResponse(
        data=BudgetStatusData(
            team_id=str_id(team.id),
            budget=BudgetStatusBudget(
                id=str_id(b["id"]), period_type=b["period_type"], limit_amount=b["limit_amount"], currency=b["currency"],
                period_start=b["period_start"].isoformat(), period_end=b["period_end"].isoformat(),
                period_state=b["period_state"],
            ) if b else None,
            usage=BudgetStatusUsage(
                currency=u["currency"], amount=u["amount"], basis=u["basis"], net_amount=u["net_amount"],
                as_of=iso_z(u["as_of"]), is_estimated=u["is_estimated"],
            ) if u else None,
            ratio_pct=s["ratio_pct"],
            forecast=BudgetStatusForecast(
                amount=f["amount"], ratio_pct=f["ratio_pct"], based_through=f["based_through"].isoformat()
            ) if f else None,
            thresholds=[
                BudgetStatusThreshold(percent=t["percent"], crossed=t["crossed"], crossed_at=iso_z(t["crossed_at"]), notified=t["notified"])
                for t in s["thresholds"]
            ],
            excluded_accounts=[
                BudgetStatusExcludedAccount(
                    cloud_account_id=str_id(e["cloud_account_id"]), provider=e["provider"],
                    account_label=e["account_label"], currency=e["currency"], reason=e["reason"],
                )
                for e in s["excluded_accounts"]
            ],
            computable=s["computable"],
            reason_code=s["reason_code"],
            staleness_threshold_hours=s["staleness_threshold_hours"],
        )
    )
