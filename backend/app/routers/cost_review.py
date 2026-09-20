"""급증 탐지 + 검토 큐 API(PR 8 — docs/비용_개발문서/05_API계약.md §7-1·§7-2, v1.2 §11-11).

- `GET /cost-anomalies`: 저장 테이블 없음 — 요청마다 규칙을 적용해 계산(app/cost/anomaly.py).
- `GET/POST /cost-review-items` · `PATCH /cost-review-items/{id}`: POST는 멱등(기존 항목 200). 새로 만들
  때만 알림 1회(app/cost/review.py). 감사 기록(`cost_review.update`)은 §14 승인 전이라 만들지 않는다 —
  updated_at/resolved_at만 남긴다.
- **DB만 읽는다**(ADR-040) — CSP import 금지.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.cost.anomaly import SOURCE_TYPE, detect_anomalies, evaluate_account, parse_source_key
from app.cost.coverage import eligible_end_for_today, utc_today
from app.cost.query import CostQuery, owned_accounts, parse_period, resolve_team_scope
from app.cost.review import create_item_with_notification, find_item
from app.db import get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.models import CloudAccount, CostReviewItem, User
from app.schemas.cost_review import (
    AnomaliesData,
    AnomaliesResponse,
    AnomalyItemOut,
    AnomalyReviewBrief,
    AnomalyRule,
    HeldDay,
    HeldOut,
    InsufficientHistoryOut,
    RelatedChange,
    ReviewItemCreateRequest,
    ReviewItemDetailResponse,
    ReviewItemListData,
    ReviewItemListResponse,
    ReviewItemOut,
    ReviewItemPatchRequest,
    UnsupportedCurrencyOut,
)
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["cost-review"])


def _parse_int_list(values: list[str], field: str) -> list[int]:
    try:
        return [int(v) for v in values]
    except ValueError as exc:
        raise validation_error(f"{field}는 숫자 ID여야 합니다.", details=[{"field": field, "reason": "invalid"}]) from exc


def _scope_accounts(
    db: Session, user: User, provider: list[str], cloud_account_id: list[str], team_id: list[str],
    period_start: dt.date, period_end: dt.date,
) -> list[CloudAccount]:
    account_ids = _parse_int_list(cloud_account_id, "cloud_account_id") if cloud_account_id else []
    if team_id:
        account_ids = resolve_team_scope(db, user.id, team_id, account_ids)
    q = CostQuery(period_start=period_start, period_end=period_end, providers=provider, cloud_account_ids=account_ids)
    return owned_accounts(db, user.id, q)


# --- 급증 탐지 ------------------------------------------------------------------------------


@router.get("/cost-anomalies", response_model=AnomaliesResponse)
def get_cost_anomalies(
    period_start: str | None = None,
    period_end: str | None = None,
    provider: list[str] = Query(default=[]),
    cloud_account_id: list[str] = Query(default=[]),
    team_id: list[str] = Query(default=[]),
    status: str = "open",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnomaliesResponse:
    if status not in ("open", "resolved", "all"):
        raise validation_error("status는 open|resolved|all 중 하나여야 합니다.", details=[{"field": "status", "reason": "invalid"}])
    start, end = parse_period(period_start, period_end)
    accounts = _scope_accounts(db, current_user, provider, cloud_account_id, team_id, start, end)
    d = detect_anomalies(db, current_user.id, accounts, start, end, status=status)
    return AnomaliesResponse(data=AnomaliesData(
        rule=AnomalyRule(**d["rule"]),
        items=[
            AnomalyItemOut(
                source_key=it["source_key"], cloud_account_id=str_id(it["cloud_account_id"]), provider=it["provider"],
                service=it["service"], date=it["date"].isoformat(), currency=it["currency"], amount=it["amount"],
                baseline_amount=it["baseline_amount"], delta=it["delta"], delta_pct=it["delta_pct"],
                review=AnomalyReviewBrief(item_id=str_id(it["review"]["item_id"]), status=it["review"]["status"], resolution=it["review"]["resolution"]) if it["review"] else None,
                related_changes=[RelatedChange(type=c["type"], id=str_id(c["id"]), service_code=c["service_code"], finished_at=iso_z(c["finished_at"])) for c in it["related_changes"]],
                label=it["label"], is_sample_data=it["is_sample_data"],
            )
            for it in d["items"]
        ],
        insufficient_history=[InsufficientHistoryOut(cloud_account_id=str_id(h["cloud_account_id"]), days_available=h["days_available"], days_required=h["days_required"]) for h in d["insufficient_history"]],
        held=[HeldOut(cloud_account_id=str_id(h["cloud_account_id"]), days=[HeldDay(date=x["date"].isoformat(), reason=x["reason"]) for x in h["days"]]) for h in d["held"]],
        unsupported_currency=[UnsupportedCurrencyOut(cloud_account_id=str_id(u["cloud_account_id"]), provider=u["provider"], currency=u["currency"]) for u in d["unsupported_currency"]],
        total=d["total"],
    ))


# --- 검토 큐 --------------------------------------------------------------------------------


def _serialize_item(item: CostReviewItem) -> ReviewItemOut:
    return ReviewItemOut(
        id=str_id(item.id), source_type=item.source_type, source_key=item.source_key, status=item.status,
        resolution=item.resolution, note=item.note, resolved_at=iso_z(item.resolved_at),
        created_at=iso_z(item.created_at), updated_at=iso_z(item.updated_at),
    )


def _get_owned_item(db: Session, user: User, item_id: str) -> CostReviewItem:
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        raise ApiError(404, "COST_REVIEW_ITEM_NOT_FOUND", "검토 항목을 찾을 수 없습니다.")
    item = db.query(CostReviewItem).filter(CostReviewItem.id == iid, CostReviewItem.user_id == user.id).first()
    if item is None:
        raise ApiError(404, "COST_REVIEW_ITEM_NOT_FOUND", "검토 항목을 찾을 수 없습니다.")
    return item


@router.get("/cost-review-items", response_model=ReviewItemListResponse)
def list_review_items(
    status: str = "open",
    provider: list[str] = Query(default=[]),
    cloud_account_id: list[str] = Query(default=[]),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewItemListResponse:
    """기간 필터는 없다(미처리 전부, 04 §4-7). `status=open`은 종결되지 않은 것(open·investigating)."""
    if status not in ("open", "investigating", "resolved", "all"):
        raise validation_error("status는 open|investigating|resolved|all 중 하나여야 합니다.", details=[{"field": "status", "reason": "invalid"}])
    query = db.query(CostReviewItem).filter(CostReviewItem.user_id == current_user.id)
    if status == "open":
        query = query.filter(CostReviewItem.status.in_(("open", "investigating")))
    elif status != "all":
        query = query.filter(CostReviewItem.status == status)
    rows = query.order_by(CostReviewItem.id.desc()).all()

    # CSP·계정 필터는 source_key의 계정 id로 건다(cost_anomaly 키 형식 고정).
    wanted_ids = set(_parse_int_list(cloud_account_id, "cloud_account_id")) if cloud_account_id else None
    provider_of: dict[int, str] = {}
    if provider:
        provider_of = {a.id: a.provider for a in db.query(CloudAccount).filter(CloudAccount.user_id == current_user.id).all()}
    items = []
    for r in rows:
        if wanted_ids is not None or provider:
            try:
                acc_id, _, _ = parse_source_key(r.source_key)
            except ValueError:
                continue
            if wanted_ids is not None and acc_id not in wanted_ids:
                continue
            if provider and provider_of.get(acc_id) not in provider:
                continue
        items.append(_serialize_item(r))
    return ReviewItemListResponse(data=ReviewItemListData(items=items, total=len(items)))


@router.post("/cost-review-items", response_model=ReviewItemDetailResponse)
def create_review_item(
    payload: ReviewItemCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewItemDetailResponse:
    """멱등 — 같은 (user, source_type, source_key)가 있으면 그 항목을 200으로 돌려준다(재알림 없음).
    새로 만들 때는 서버가 그 날의 급증을 다시 계산해 알림 금액을 채운다 — 클라이언트 값은 쓰지 않는다."""
    existing = find_item(db, current_user.id, payload.source_type, payload.source_key)
    if existing is not None:
        return ReviewItemDetailResponse(data=_serialize_item(existing))

    try:
        account_id, service, day = parse_source_key(payload.source_key)
    except ValueError as exc:
        raise validation_error("source_key는 {cloud_account_id}:{service}:{YYYY-MM-DD} 형식이어야 합니다.", details=[{"field": "source_key", "reason": "invalid"}]) from exc
    account = db.query(CloudAccount).filter(CloudAccount.id == account_id, CloudAccount.user_id == current_user.id).first()
    if account is None:
        raise ApiError(404, "CLOUD_ACCOUNT_NOT_FOUND", "클라우드 계정을 찾을 수 없습니다.")
    if day >= eligible_end_for_today():
        raise validation_error("아직 판정 대상이 아닌 날짜입니다(최근 3일 제외).", details=[{"field": "source_key", "reason": "not_yet_eligible"}])

    ev = evaluate_account(db, account, day, day + dt.timedelta(days=1))
    match = next((it for it in ev.items if it.service == service), None)
    if match is None:
        # 현재 규칙으로 급증이 아닌 키는 큐에 넣지 않는다 — 금액 없는 항목이 생기면 화면이 0원이나
        # "현재 급증"으로 오해한다(D10).
        raise validation_error("현재 규칙으로 탐지된 급증이 아닙니다.", details=[{"field": "source_key", "reason": "not_current_anomaly"}])

    item, _created = create_item_with_notification(db, current_user.id, anomaly=match, account=account, note=payload.note)
    db.commit()
    db.refresh(item)
    return ReviewItemDetailResponse(data=_serialize_item(item))


@router.patch("/cost-review-items/{item_id}", response_model=ReviewItemDetailResponse)
def patch_review_item(
    item_id: str,
    payload: ReviewItemPatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewItemDetailResponse:
    item = _get_owned_item(db, current_user, item_id)
    new_status = payload.status or item.status
    if item.status == "resolved" and new_status != "resolved":
        # 재발은 새 항목이다(확정 14) — 종결된 것을 되돌리지 않는다.
        raise ApiError(409, "CONFLICT", "종결된 항목은 되돌릴 수 없습니다. 재발은 새 항목으로 등록됩니다.", details=[{"field": "status", "reason": "already_resolved"}])
    if new_status == "resolved":
        resolution = payload.resolution or item.resolution
        if not resolution:
            raise validation_error("resolved로 바꾸려면 resolution이 필요합니다.", details=[{"field": "resolution", "reason": "required"}])
        item.resolution = resolution
        if item.status != "resolved":
            item.resolved_at = dt.datetime.now(dt.timezone.utc)
    else:
        if payload.resolution is not None:
            item.resolution = payload.resolution
        item.resolved_at = None
    item.status = new_status
    if payload.note is not None:
        item.note = payload.note
    db.flush()
    db.commit()
    db.refresh(item)
    return ReviewItemDetailResponse(data=_serialize_item(item))
