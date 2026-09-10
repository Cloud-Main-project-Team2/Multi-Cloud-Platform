"""API 명세서 v1.1 §8 인벤토리 API — INV-01/INV-02 화면용.

DB에 저장된 최신 snapshot을 그대로 반환한다(요청마다 CSP API를 호출하지 않는다). §15에 따라
모든 조회는 `resources.cloud_account_id -> cloud_accounts.user_id` 관계로 소유권을 확인한다.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.models import CloudAccount, Resource, ServiceCatalog, User
from app.schemas.resources import (
    CloudAccountBrief,
    CostSummary,
    ProviderCount,
    ResourceListData,
    ResourceListResponse,
    ResourceOut,
    ResourceResponse,
    ResourceSummaryData,
    ResourceSummaryResponse,
    ServiceBrief,
)
from app.serialization import decimal_str, iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["resources"])

_SEARCH_FIELDS = {"resource", "service", "region", "account"}


def _parse_id(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다.") from exc


def _parse_int_list(values: list[str], field: str) -> list[int]:
    try:
        return [int(v) for v in values]
    except ValueError as exc:
        raise validation_error(f"{field}는 숫자 ID여야 합니다.", details=[{"field": field, "reason": "invalid"}]) from exc


# --- 조회 필터 ---------------------------------------------------------------------------


def resource_filters(
    provider: list[str] | None = Query(default=None),
    service_catalog_id: list[str] | None = Query(default=None),
    cloud_account_id: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    region: list[str] | None = Query(default=None),
    search_field: str | None = Query(default=None),
    q: str | None = Query(default=None),
    include_stale: bool = Query(default=False),
    include_deleted: bool = Query(default=False),
) -> dict:
    if search_field is not None and search_field not in _SEARCH_FIELDS:
        raise validation_error(
            "지원하지 않는 search_field입니다.", details=[{"field": "search_field", "reason": "invalid"}]
        )
    return {
        "provider": provider,
        "service_catalog_id": service_catalog_id,
        "cloud_account_id": cloud_account_id,
        "status": status,
        "region": region,
        "search_field": search_field,
        "q": q,
        "include_stale": include_stale,
        "include_deleted": include_deleted,
    }


def _base_query(db: Session, user_id: int):
    return (
        db.query(Resource, CloudAccount, ServiceCatalog)
        .join(CloudAccount, Resource.cloud_account_id == CloudAccount.id)
        .join(ServiceCatalog, Resource.service_catalog_id == ServiceCatalog.id)
        .filter(CloudAccount.user_id == user_id)
    )


def _apply_filters(query, filters: dict):
    if filters["provider"]:
        query = query.filter(CloudAccount.provider.in_(filters["provider"]))
    if filters["service_catalog_id"]:
        query = query.filter(
            Resource.service_catalog_id.in_(_parse_int_list(filters["service_catalog_id"], "service_catalog_id"))
        )
    if filters["cloud_account_id"]:
        query = query.filter(
            Resource.cloud_account_id.in_(_parse_int_list(filters["cloud_account_id"], "cloud_account_id"))
        )
    if filters["status"]:
        query = query.filter(Resource.status.in_(filters["status"]))
    if filters["region"]:
        query = query.filter(Resource.region.in_(filters["region"]))
    if not filters["include_stale"]:
        query = query.filter(Resource.is_stale.is_(False))
    if not filters["include_deleted"]:
        query = query.filter(Resource.deleted_at.is_(None))

    q = (filters["q"] or "").strip()
    if q:
        pattern = f"%{q}%"
        field = filters["search_field"] or "resource"
        if field == "resource":
            query = query.filter(
                sa.or_(
                    Resource.name.ilike(pattern),
                    Resource.external_resource_id.ilike(pattern),
                    Resource.original_resource_type.ilike(pattern),
                )
            )
        elif field == "service":
            query = query.filter(
                sa.or_(ServiceCatalog.display_name.ilike(pattern), ServiceCatalog.service_code.ilike(pattern))
            )
        elif field == "region":
            query = query.filter(Resource.region.ilike(pattern))
        elif field == "account":
            query = query.filter(
                sa.or_(CloudAccount.account_label.ilike(pattern), CloudAccount.external_account_id.ilike(pattern))
            )
    return query


# --- 직렬화 ---------------------------------------------------------------------------


def _serialize_cost_summary(resource: Resource) -> CostSummary | None:
    if resource.estimated_monthly_cost is None and resource.collected_cost_amount is None:
        return None
    return CostSummary(
        estimated_monthly_cost=decimal_str(resource.estimated_monthly_cost),
        collected_cost_amount=decimal_str(resource.collected_cost_amount),
        currency=resource.cost_currency,
        period_start=resource.cost_period_start.isoformat() if resource.cost_period_start else None,
        period_end=resource.cost_period_end.isoformat() if resource.cost_period_end else None,
        as_of=iso_z(resource.cost_as_of),
        source=resource.cost_source,
    )


def _serialize_resource(resource: Resource, account: CloudAccount, service: ServiceCatalog) -> ResourceOut:
    return ResourceOut(
        id=str_id(resource.id),
        cloud_account=CloudAccountBrief(
            id=str_id(account.id),
            provider=account.provider,
            external_account_id=account.external_account_id,
            account_label=account.account_label,
        ),
        service=ServiceBrief(
            id=str_id(service.id),
            service_code=service.service_code,
            category=service.category,
            display_name=service.display_name,
        ),
        external_resource_id=resource.external_resource_id,
        original_resource_type=resource.original_resource_type,
        name=resource.name,
        region=resource.region,
        status=resource.status,
        cost_summary=_serialize_cost_summary(resource),
        tags=resource.tags,
        first_seen_at=iso_z(resource.first_seen_at),
        last_seen_at=iso_z(resource.last_seen_at),
        is_stale=resource.is_stale,
        deleted_at=iso_z(resource.deleted_at),
        last_synced_at=iso_z(resource.last_synced_at),
    )


# --- GET /resources, /resources/summary, /resources/{id} -------------------------------


@router.get("/resources", response_model=ResourceListResponse)
def list_resources(
    filters: dict = Depends(resource_filters),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResourceListResponse:
    query = _apply_filters(_base_query(db, current_user.id), filters)
    rows = query.order_by(Resource.id).all()
    items = [_serialize_resource(r, a, s) for r, a, s in rows]
    return ResourceListResponse(data=ResourceListData(items=items, total=len(items)))


@router.get("/resources/summary", response_model=ResourceSummaryResponse)
def resources_summary(
    filters: dict = Depends(resource_filters),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResourceSummaryResponse:
    query = _apply_filters(_base_query(db, current_user.id), filters)
    rows = query.all()

    total = len(rows)
    active = sum(1 for r, _, _ in rows if not r.is_stale and r.deleted_at is None)
    stale = sum(1 for r, _, _ in rows if r.is_stale)

    # last_synced_at은 resource_sync_jobs가 아니라 리소스별 last_synced_at의 최댓값을 쓴다
    # — 목업 시딩의 sync_job은 아직 완료(finished_at)되지 않은 상태라 참고용일 뿐이고,
    # 실제로 각 리소스가 언제 갱신됐는지는 resources.last_synced_at이 더 정확하다(CLAUDE.md 기록).
    synced_at_values = [r.last_synced_at for r, _, _ in rows if r.last_synced_at is not None]
    last_synced_at = max(synced_at_values) if synced_at_values else None

    counts: dict[str, int] = {}
    for _, account, _ in rows:
        counts[account.provider] = counts.get(account.provider, 0) + 1
    by_provider = [ProviderCount(provider=p, count=c) for p, c in sorted(counts.items())]

    return ResourceSummaryResponse(
        data=ResourceSummaryData(
            total_resources=total,
            active_resources=active,
            stale_resources=stale,
            last_synced_at=iso_z(last_synced_at),
            by_provider=by_provider,
        )
    )


@router.get("/resources/{resource_id}", response_model=ResourceResponse)
def get_resource(
    resource_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResourceResponse:
    rid = _parse_id(resource_id)
    row = (
        db.query(Resource, CloudAccount, ServiceCatalog)
        .join(CloudAccount, Resource.cloud_account_id == CloudAccount.id)
        .join(ServiceCatalog, Resource.service_catalog_id == ServiceCatalog.id)
        .filter(Resource.id == rid, CloudAccount.user_id == current_user.id)
        .one_or_none()
    )
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다.")
    resource, account, service = row
    return ResourceResponse(data=_serialize_resource(resource, account, service))
