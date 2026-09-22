"""보고서 API — "정기 발송" 설정(`/reports/settings`)과 "생성 이력"(`/reports`) 2026-09-19.

- **정기 발송 설정**: 지금까지 브라우저 localStorage에만 있어서 백엔드 스케줄러가 "누구에게
  언제 보낼지" 알 방법이 없었다. 사용자당 1행(UNIQUE user_id) — 저장된 행이 없으면 "웹
  다운로드/주간"을 기본값으로 돌려준다. `app/report_scheduler.py`가 이 값을 읽어 실제로
  메일을 보낸다.
- **생성 이력**: 실제로 "생성하기"를 누른 기록. `(user_id, period_type, period_from,
  period_to, providers)` UNIQUE + `ON CONFLICT DO UPDATE`로 같은 조건 재생성은 새 행을
  만들지 않는다. 비용 요약(`cost_snapshot`)은 이때 `app/report_cost.py`로 계산해 그대로
  저장한다(생성 시점 고정 — app/models.py::ReportGeneration 참고).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.logging_config import log_business_event
from app.models import Notification, ReportDeliverySetting, ReportGeneration, User
from app.report_cost import build_cost_snapshot
from app.schemas.reports import (
    ReportGenerationCreate,
    ReportGenerationDeleteData,
    ReportGenerationDeleteResponse,
    ReportGenerationListData,
    ReportGenerationListResponse,
    ReportGenerationOut,
    ReportGenerationResponse,
    ReportSettingsIn,
    ReportSettingsOut,
    ReportSettingsResponse,
)
from app.serialization import iso_z, str_id

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


# ── "생성 이력"(2026-09-19) ──────────────────────────────────────────────
# 지금까지 브라우저 localStorage에만 있어서 팀원끼리 공유가 안 되고, 같은 조건으로 다시
# 생성할 때마다 새 행이 계속 쌓여 지저분해지는 문제가 있었다(사용자 실사용 중 확인). 여기서부터
# 실 저장소로 옮긴다 — app/models.py::ReportGeneration 참고.


def _canonical_providers(clouds: list[str]) -> str:
    # 선택 순서가 달라도(azure,aws vs aws,azure) 같은 조합이면 같은 문자열이 되게 정규화한다 —
    # 안 그러면 DB UNIQUE가 "같은 조건"을 못 잡아서 중복 방지가 무력화된다.
    return ",".join(sorted(set(clouds)))


def _serialize_generation(row: ReportGeneration) -> ReportGenerationOut:
    return ReportGenerationOut(
        id=str_id(row.id),
        period_type=row.period_type,
        period_from=row.period_from.isoformat(),
        period_to=row.period_to.isoformat(),
        clouds=row.providers.split(","),
        generated_at=iso_z(row.generated_at),
        created_at=iso_z(row.created_at),
        cost_snapshot=row.cost_snapshot,
    )


def _parse_report_id(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(404, "REPORT_NOT_FOUND", "보고서 이력을 찾을 수 없습니다.") from exc


@router.post("/reports", response_model=ReportGenerationResponse)
def create_report_generation(
    payload: ReportGenerationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportGenerationResponse:
    """"생성하기"를 누를 때마다 호출한다 — 같은 조건(기간 종류·기간·클라우드 조합)으로 이미
    생성한 적 있으면 새 행을 추가하지 않고 `generated_at`만 지금 시각으로 갱신한다(그래서
    "생성 이력"에 같은 조건이 여러 줄로 쌓이지 않는다). 기간이 다르면(예: 다음 날 다시 생성)
    별개 행이다 — 실제로 다른 보고서라 중복이 아니다."""
    try:
        period_from = dt.date.fromisoformat(payload.period_from)
        period_to = dt.date.fromisoformat(payload.period_to)
    except ValueError as exc:
        raise validation_error("period_from/period_to는 YYYY-MM-DD 형식이어야 합니다.") from exc

    now = dt.datetime.now(dt.timezone.utc)
    providers = _canonical_providers(payload.clouds)

    # 비용 요약은 생성 시점에 고정 계산해서 저장한다(app/report_cost.py 참고, 재조회하지 않음).
    # 계산이 실패해도(예: 비용 파트 함수의 예외) 보고서 생성 자체는 실패시키지 않는다 — 비용
    # 섹션만 "불러오지 못함"으로 남긴다(비용 파트 요구사항 §9 — 다른 영역까지 실패하면 안 됨).
    try:
        cost_snapshot = build_cost_snapshot(db, current_user, period_from, period_to, payload.clouds)
    except Exception:
        log_business_event(
            "report.cost_snapshot_failed", level="ERROR", exc_info=True,
            user_id=current_user.id, period_type=payload.period_type,
        )
        cost_snapshot = None

    stmt = (
        pg_insert(ReportGeneration)
        .values(
            user_id=current_user.id,
            period_type=payload.period_type,
            period_from=period_from,
            period_to=period_to,
            providers=providers,
            generated_at=now,
            cost_snapshot=cost_snapshot,
        )
        .on_conflict_do_update(
            constraint="uq_report_generations_params",
            set_={"generated_at": now, "cost_snapshot": cost_snapshot},
        )
        .returning(ReportGeneration.id)
    )
    # 완료 알림 — "생성하기"가 요청/응답 안에서 즉시 끝나 별도 job이 없으므로(item 2), 사용자가
    # 결과 탭을 기다리지 못하고 페이지를 벗어나도 알림함에서 확인할 수 있게 성공/실패를 함께 남긴다.
    params: dict = {
        "period_type": payload.period_type, "period_from": payload.period_from, "period_to": payload.period_to,
    }
    try:
        row_id = db.execute(stmt).scalar_one()
    except Exception:
        db.rollback()
        db.add(
            Notification(
                user_id=current_user.id,
                type="report_generation_failed",
                reference_type="report_generation",
                reference_id=None,
                message_key="notif.report.failed",
                message_params=params,
            )
        )
        db.commit()
        raise

    row = db.get(ReportGeneration, row_id)
    db.add(
        Notification(
            user_id=current_user.id,
            type="report_generated",
            reference_type="report_generation",
            reference_id=row.id,
            message_key="notif.report.succeeded",
            message_params=params,
        )
    )
    db.commit()
    return ReportGenerationResponse(data=_serialize_generation(row))


@router.get("/reports", response_model=ReportGenerationListResponse)
def list_report_generations(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportGenerationListResponse:
    rows = (
        db.query(ReportGeneration)
        .filter(ReportGeneration.user_id == current_user.id)
        .order_by(ReportGeneration.generated_at.desc())
        .limit(limit)
        .all()
    )
    return ReportGenerationListResponse(data=ReportGenerationListData(items=[_serialize_generation(r) for r in rows]))


@router.get("/reports/{report_id}", response_model=ReportGenerationResponse)
def get_report_generation(
    report_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportGenerationResponse:
    row = (
        db.query(ReportGeneration)
        .filter(ReportGeneration.id == _parse_report_id(report_id), ReportGeneration.user_id == current_user.id)
        .one_or_none()
    )
    if row is None:
        raise ApiError(404, "REPORT_NOT_FOUND", "보고서 이력을 찾을 수 없습니다.")
    return ReportGenerationResponse(data=_serialize_generation(row))


@router.delete("/reports/{report_id}", response_model=ReportGenerationDeleteResponse)
def delete_report_generation(
    report_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportGenerationDeleteResponse:
    row = (
        db.query(ReportGeneration)
        .filter(ReportGeneration.id == _parse_report_id(report_id), ReportGeneration.user_id == current_user.id)
        .one_or_none()
    )
    if row is None:
        raise ApiError(404, "REPORT_NOT_FOUND", "보고서 이력을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return ReportGenerationDeleteResponse(data=ReportGenerationDeleteData(id=report_id, deleted=True))
