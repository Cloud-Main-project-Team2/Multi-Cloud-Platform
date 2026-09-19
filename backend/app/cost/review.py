"""검토 큐 항목 생성 = 알림 1회 (PR 8 · 06 §4-2 · 2026-09-19 D2·D3 결정).

자동 탐지든 사용자의 수동 POST든 **항목이 새로 생성될 때만** 같은 트랜잭션에 `notifications` 1행을
만든다. 이미 있는 항목은 그대로 돌려주고 재알림하지 않는다 — "(계정·서비스·날짜) 최초 1회 알림"을
`uq_cost_review_items_user_source`가 보장한다. 수동으로 먼저 큐에 넣었을 때 자동 탐지가 UNIQUE에
막혀 알림을 영원히 생략하는 구멍은, 수동 생성도 같은 헬퍼를 지나 알림을 만들기 때문에 생기지 않는다.

- 알림 저장이 실패하면 예외가 그대로 올라가 호출부 트랜잭션이 통째로 취소된다 → 큐 항목도 남지 않는다.
- UNIQUE 위반과 다른 DB 오류를 구분한다 — 제약 이름이 다르면 다시 올린다.
- 알림 금액은 서버가 계산한 AnomalyItem에서만 온다(클라이언트 값을 쓰지 않는다).
- 커밋은 호출부가 한다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cost.anomaly import SOURCE_TYPE, AnomalyItem
from app.cost.query import money
from app.logging_config import log_business_event
from app.models import CloudAccount, CostReviewItem, Notification

NOTIFICATION_TYPE = "cost_anomaly"
MESSAGE_KEY = "notif.cost.anomaly"
REFERENCE_TYPE = "cost_review_item"
UNIQUE_CONSTRAINT = "uq_cost_review_items_user_source"


def _is_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name:
        return name == UNIQUE_CONSTRAINT
    return UNIQUE_CONSTRAINT in str(exc)


def find_item(db: Session, user_id: int, source_type: str, source_key: str) -> CostReviewItem | None:
    return (
        db.query(CostReviewItem)
        .filter(CostReviewItem.user_id == user_id, CostReviewItem.source_type == source_type, CostReviewItem.source_key == source_key)
        .first()
    )


def create_item_with_notification(
    db: Session,
    user_id: int,
    *,
    anomaly: AnomalyItem,
    account: CloudAccount,
    note: str | None = None,
) -> tuple[CostReviewItem, bool]:
    """(항목, 새로 만들었는가). 새로 만들었을 때만 알림이 함께 생긴다."""
    key = anomaly.source_key
    item = CostReviewItem(user_id=user_id, source_type=SOURCE_TYPE, source_key=key, note=note)
    try:
        with db.begin_nested():  # SAVEPOINT — UNIQUE 위반이 바깥 트랜잭션을 망가뜨리지 않게
            db.add(item)
            db.flush()
    except IntegrityError as exc:
        if not _is_unique_violation(exc):
            raise
        existing = find_item(db, user_id, SOURCE_TYPE, key)
        if existing is None:  # 동시성 — 방금 지워졌다면 다시 시도하지 않고 오류로 올린다
            raise
        return existing, False

    notification = Notification(
        user_id=user_id,
        type=NOTIFICATION_TYPE,
        reference_type=REFERENCE_TYPE,
        reference_id=item.id,
        message_key=MESSAGE_KEY,
        message_params={
            "review_item_id": str(item.id),
            "cloud_account_id": str(account.id),
            "provider": account.provider,
            "account_label": account.account_label or account.external_account_id,
            "service": anomaly.service,
            "date": anomaly.date.isoformat(),
            "currency": anomaly.currency,
            "amount": money(anomaly.amount),
            "baseline_amount": money(anomaly.baseline_amount),
            "delta": money(anomaly.delta),
            "delta_pct": None if anomaly.delta_pct is None else str(anomaly.delta_pct),  # null = 기준선 0(신규 비용 발생)
        },
    )
    db.add(notification)
    db.flush()
    log_business_event(
        "cost.anomaly.notified", user_id=user_id, review_item_id=item.id, cloud_account_id=account.id,
        service=anomaly.service, date=anomaly.date.isoformat(), delta=money(anomaly.delta), currency=anomaly.currency,
    )
    return item, True


def evaluate_and_notify_account(db: Session, account: CloudAccount, today: dt.date | None = None) -> list[CostReviewItem]:
    """자동 탐지 경로 — 저장된 판정 대상 날 전부를 평가해 새 급증마다 큐 항목 + 알림. 커밋은 호출부."""
    from app.cost.anomaly import evaluate_account_all_stored

    ev = evaluate_account_all_stored(db, account, today)
    created: list[CostReviewItem] = []
    for it in ev.items:
        item, is_new = create_item_with_notification(db, account.user_id, anomaly=it, account=account)
        if is_new:
            created.append(item)
    return created


def evaluate_and_notify_for_account_safely(db: Session, account: CloudAccount) -> list[CostReviewItem]:
    """수집 성공 훅 — 예외는 기록만 하고 삼킨다(탐지 버그가 수집 결과를 되돌리면 안 된다). 커밋 포함."""
    try:
        created = evaluate_and_notify_account(db, account)
        db.commit()
        return created
    except Exception:  # noqa: BLE001
        db.rollback()
        log_business_event("cost.anomaly.evaluate_failed", level="ERROR", cloud_account_id=account.id, exc_info=True)
        return []
