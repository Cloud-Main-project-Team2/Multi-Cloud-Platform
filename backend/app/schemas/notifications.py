"""알림 조회/읽음 처리 스키마 — 프로비저닝 성공/실패 알림(§14)을 프론트가 조회한다.

알림 row 생성은 provisioning 라우터(`_create_notification`)가 담당하고, 여기서는 조회와
읽음 처리만 정의한다. 메시지 문구는 `message_key`+`message_params`로 내려 주고 표시는
프론트가 담당한다(백엔드는 i18n 문구를 들지 않는다).
"""

from __future__ import annotations

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: str
    type: str
    reference_type: str | None
    reference_id: str | None
    message_key: str
    message_params: dict
    is_read: bool
    read_at: str | None
    created_at: str | None


class NotificationListData(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class NotificationListResponse(BaseModel):
    data: NotificationListData


class MarkReadData(BaseModel):
    unread_count: int
    updated: int


class MarkReadResponse(BaseModel):
    data: MarkReadData


class DeleteAllData(BaseModel):
    deleted: int


class DeleteAllResponse(BaseModel):
    data: DeleteAllData
