"""보고서 "정기 발송" 설정 API(2026-09-19) — `GET`/`PUT /reports/settings`.

지금까지 정기 발송 설정(웹 다운로드/메일 전송, 수신 주소, 주기)은 브라우저 localStorage에만
있어서 백엔드 스케줄러가 "누구에게 언제 보낼지" 알 방법이 없었다. 이 라우터가 실 저장소를
제공하고, `app/report_scheduler.py`가 여기 저장된 값을 읽어 실제로 메일을 보낸다.

사용자당 1행(UNIQUE user_id) — 저장된 행이 없으면 "웹 다운로드/주간"을 기본값으로 돌려준다
(아직 아무것도 설정한 적 없는 상태).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import ReportDeliverySetting, User
from app.schemas.reports import ReportSettingsIn, ReportSettingsOut, ReportSettingsResponse
from app.serialization import iso_z

router = APIRouter(prefix="/api/v1", tags=["reports"])


def _send_hour_kst() -> int:
    # 한국은 서머타임이 없어 UTC+9 고정 오프셋이 항상 정확하다(app/logging_config.py의 ts와
    # 동일한 원칙 — zoneinfo 없이도 안전).
    return (get_settings().report_send_hour_utc + 9) % 24


def _serialize(row: ReportDeliverySetting | None) -> ReportSettingsOut:
    send_hour_kst = _send_hour_kst()
    if row is None:
        return ReportSettingsOut(
            delivery_method="WEB", email=None, period_type="WEEKLY", last_sent_at=None,
            send_hour_kst=send_hour_kst,
        )
    return ReportSettingsOut(
        delivery_method=row.delivery_method, email=row.email, period_type=row.period_type,
        last_sent_at=iso_z(row.last_sent_at), send_hour_kst=send_hour_kst,
    )


@router.get("/reports/settings", response_model=ReportSettingsResponse)
def get_report_settings(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ReportSettingsResponse:
    row = db.query(ReportDeliverySetting).filter_by(user_id=current_user.id).one_or_none()
    return ReportSettingsResponse(data=_serialize(row))


@router.put("/reports/settings", response_model=ReportSettingsResponse)
def put_report_settings(
    payload: ReportSettingsIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportSettingsResponse:
    row = db.query(ReportDeliverySetting).filter_by(user_id=current_user.id).one_or_none()
    email = payload.email.strip() if payload.email else None
    if row is None:
        row = ReportDeliverySetting(
            user_id=current_user.id, delivery_method=payload.delivery_method, email=email,
            period_type=payload.period_type,
        )
        db.add(row)
    else:
        # last_sent_at은 그대로 둔다 — 스케줄러가 다음 실행 때 새 period_type 기준으로 다시
        # 판단한다(예: WEEKLY→DAILY로 바꿔도 "저장 직후 밀린 발송"으로 오인해 즉시 보내지
        # 않는다).
        row.delivery_method = payload.delivery_method
        row.email = email
        row.period_type = payload.period_type
    db.commit()
    db.refresh(row)
    return ReportSettingsResponse(data=_serialize(row))


@router.delete("/reports/settings", response_model=ReportSettingsResponse)
def disable_report_settings(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ReportSettingsResponse:
    """"발송 해제" — 저장된 설정 자체를 지운다(웹 다운로드/주간 기본값으로 되돌아간다)."""
    row = db.query(ReportDeliverySetting).filter_by(user_id=current_user.id).one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
    return ReportSettingsResponse(data=_serialize(None))
