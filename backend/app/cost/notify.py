"""예산 임계(80%·100%) 알림 — 중복 방지 기록과 알림을 같은 트랜잭션에(PR 7, 08 §6-3 · ADR-037).

- (예산·기간·임계치)당 1회. `team_budget_notifications`의 UNIQUE 제약이 판정하고, INSERT가
  성공했을 때만 `notifications` 행을 만든다. 실패하면 둘 다 취소돼 다음 평가에서 다시 시도된다.
- 평가 시점: 자동·수동 수집 성공, 예산 생성, 계정 배정 변경, 현재 예산을 바꾸는 쓰기 작업 뒤.
  `GET budget-status`는 평가하지 않는다(조회가 부작용을 만들지 않게).
- in_progress이며 computable인 예산만 본다. 처음부터 100% 이상이면 80·100을 각각 한 번 만든다.
- 기존 `notifications` 테이블 재사용 — 비용 전용 알림 인프라·조회 API를 만들지 않는다.
- 커밋은 호출부가 한다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cost.budget import STATE_IN_PROGRESS, THRESHOLDS, compute_budget_status
from app.logging_config import log_business_event
from app.models import CloudAccount, Notification, Team, TeamBudget, TeamBudgetNotification

NOTIFICATION_TYPE = "budget_threshold"
MESSAGE_KEY = "notif.budget.threshold"
REFERENCE_TYPE = "team_budget"


def evaluate_budget_thresholds(db: Session, team: Team, today: dt.date | None = None) -> list[Notification]:
    """팀 하나의 현재 예산을 판정해 새로 넘긴 임계치마다 알림을 만든다. 만든 알림 목록을 돌려준다
    (이미 보낸 임계치는 빈 목록)."""
    status = compute_budget_status(db, team, today)
    budget_info = status["budget"]
    if not status["computable"] or budget_info is None or budget_info["period_state"] != STATE_IN_PROGRESS:
        return []

    budget = db.get(TeamBudget, budget_info["id"])
    if budget is None:
        return []
    ratio = Decimal(status["ratio_pct"])
    period_start: dt.date = budget_info["period_start"]

    created: list[Notification] = []
    for threshold in THRESHOLDS:
        if ratio < threshold:
            continue
        record = TeamBudgetNotification(team_budget_id=budget.id, period_start=period_start, threshold=threshold)
        try:
            with db.begin_nested():  # SAVEPOINT — UNIQUE 위반이 바깥 트랜잭션을 망가뜨리지 않게
                db.add(record)
                db.flush()
        except IntegrityError:
            continue  # 이미 보냈다 — 알림을 만들지 않는다

        notification = Notification(
            user_id=team.user_id,
            type=NOTIFICATION_TYPE,
            reference_type=REFERENCE_TYPE,
            reference_id=budget.id,
            message_key=MESSAGE_KEY,
            message_params={
                "team_id": str(team.id),
                "team_name": team.name,
                "percent": threshold,
                "limit_amount": budget_info["limit_amount"],
                "currency": budget.currency,
                "period_start": period_start.isoformat(),
                "ratio_pct": status["ratio_pct"],
            },
        )
        db.add(notification)
        db.flush()
        record.notification_id = notification.id
        db.flush()
        created.append(notification)
        log_business_event(
            "cost.budget_threshold.notified", team_id=team.id, budget_id=budget.id,
            period_start=period_start.isoformat(), threshold=threshold, ratio_pct=status["ratio_pct"],
        )
    return created


def evaluate_for_account(db: Session, account: CloudAccount) -> list[Notification]:
    """수집 성공 훅 — 그 계정이 속한 팀만 평가한다. 예외는 기록만 하고 삼킨다: 알림 판정의 버그가
    수집 결과를 되돌리면 안 된다. 커밋은 여기서 한다(수집 트랜잭션은 이미 커밋된 뒤다)."""
    if account.team_id is None:
        return []
    try:
        team = db.get(Team, account.team_id)
        if team is None:
            return []
        created = evaluate_budget_thresholds(db, team)
        db.commit()
        return created
    except Exception:  # noqa: BLE001
        db.rollback()
        log_business_event(
            "cost.budget_threshold.evaluate_failed", level="ERROR", cloud_account_id=account.id,
            team_id=account.team_id, exc_info=True,
        )
        return []
