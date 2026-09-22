"""알림 조회/읽음 처리/삭제 API — 사이드바 벨(🔔)이 조회한다.

알림 row 생성은 프로비저닝/동기화/비용/보고서 라우터가 각자 담당한다(성공/실패 시
`_create_notification` 계열). 이 라우터는 조회(GET)·읽음 처리(read-all)·삭제(개별·전체)만
제공한다. 소유권은 `notifications.user_id`로 확인한다(§15와 동일한 사용자 스코프).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.models import Notification, User
from app.schemas.notifications import (
    DeleteAllData,
    DeleteAllResponse,
    MarkReadData,
    MarkReadResponse,
    NotificationListData,
    NotificationListResponse,
    NotificationOut,
)
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["notifications"])


def _parse_notification_id(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise validation_error(
            "notification_id는 숫자 ID여야 합니다.", details=[{"field": "notification_id", "reason": "invalid"}]
        ) from exc


def _serialize(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=str_id(n.id),
        type=n.type,
        reference_type=n.reference_type,
        reference_id=str_id(n.reference_id),
        message_key=n.message_key,
        message_params=n.message_params or {},
        is_read=n.is_read,
        read_at=iso_z(n.read_at),
        created_at=iso_z(n.created_at),
    )


@router.get("/notifications", response_model=NotificationListResponse)
def list_notifications(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationListResponse:
    rows = (
        db.query(Notification)
        .filter_by(user_id=current_user.id)
        .order_by(Notification.id.desc())
        .limit(limit)
        .all()
    )
    unread = (
        db.query(Notification).filter_by(user_id=current_user.id, is_read=False).count()
    )
    return NotificationListResponse(
        data=NotificationListData(items=[_serialize(n) for n in rows], unread_count=unread)
    )


@router.post("/notifications/read-all", response_model=MarkReadResponse)
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MarkReadResponse:
    """열람 시 벨 배지의 미확인 개수를 0으로 만든다 — 미확인 알림을 전부 읽음 처리."""
    now = dt.datetime.now(dt.timezone.utc)
    updated = (
        db.query(Notification)
        .filter_by(user_id=current_user.id, is_read=False)
        .update({"is_read": True, "read_at": now}, synchronize_session=False)
    )
    db.commit()
    return MarkReadResponse(data=MarkReadData(unread_count=0, updated=updated))


@router.delete("/notifications/{notification_id}", status_code=204)
def delete_notification(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = (
        db.query(Notification)
        .filter_by(id=_parse_notification_id(notification_id), user_id=current_user.id)
        .one_or_none()
    )
    if row is None:
        raise ApiError(404, "NOTIFICATION_NOT_FOUND", "알림을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.delete("/notifications", response_model=DeleteAllResponse)
def delete_all_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteAllResponse:
    deleted = db.query(Notification).filter_by(user_id=current_user.id).delete(synchronize_session=False)
    db.commit()
    return DeleteAllResponse(data=DeleteAllData(deleted=deleted))
