"""API 명세서 v1.1 §8 인벤토리 API — INV-01/INV-02 화면용.

DB에 저장된 최신 snapshot을 그대로 반환한다(요청마다 CSP API를 호출하지 않는다). §15에 따라
모든 조회는 `resources.cloud_account_id -> cloud_accounts.user_id` 관계로 소유권을 확인한다.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import get_db
from app.deps import get_current_user, require_confirmation
from app.errors import ApiError, validation_error
from app.models import CloudAccount, Credential, Resource, ServiceCatalog, User
from app.resource_actions import ResourceActionError, perform_action, supported_actions
from app.schemas.resources import (
    ActionResultError,
    ActionResultItem,
    CloudAccountBrief,
    CostSummary,
    ProviderCount,
    ResourceActionData,
    ResourceActionRequest,
    ResourceActionResponse,
    ResourceListData,
    ResourceListResponse,
    ResourceOut,
    ResourceResponse,
    ResourceSummaryData,
    ResourceSummaryResponse,
    ServiceBrief,
)
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json
from app.serialization import decimal_str, iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["resources"])

_SEARCH_FIELDS = {"resource", "service", "region", "account"}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


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


# --- POST /resources/action -------------------------------------------------------------


def _rejected(resource_id: str, code: str) -> ActionResultItem:
    return ActionResultItem(resource_id=resource_id, status="rejected", error=ActionResultError(code=code))


def _failed(resource_id: str, code: str) -> ActionResultItem:
    return ActionResultItem(resource_id=resource_id, status="failed", error=ActionResultError(code=code))


def _process_action_item(
    db: Session, current_user: User, raw_id: str, action: str, force_empty: bool, request: Request
) -> ActionResultItem:
    try:
        resource_id = int(raw_id)
    except ValueError:
        return _rejected(raw_id, "RESOURCE_NOT_FOUND")

    row = (
        db.query(Resource, CloudAccount, ServiceCatalog)
        .join(CloudAccount, Resource.cloud_account_id == CloudAccount.id)
        .join(ServiceCatalog, Resource.service_catalog_id == ServiceCatalog.id)
        .filter(Resource.id == resource_id, CloudAccount.user_id == current_user.id)
        .one_or_none()
    )
    if row is None:
        return _rejected(raw_id, "RESOURCE_NOT_FOUND")
    resource, account, service = row
    resource_id_str = str_id(resource.id)

    def _deny(code: str) -> ActionResultItem:
        record_audit_event(
            db,
            actor_user_id=current_user.id,
            action=f"resource.{action}",
            target_type="resource",
            target_id=resource_id_str,
            result="denied",
            provider=account.provider,
            metadata={"error_code": code},
            request_id=_request_id(request),
        )
        return _rejected(resource_id_str, code)

    # 1) 서비스가 이 동작을 지원하는가
    if action not in supported_actions(account.provider, service.service_code, resource.original_resource_type):
        return _deny("UNSUPPORTED_OPERATION")

    # 2) 사용 가능한 검증 credential과 provider 권한
    credential = (
        db.query(Credential)
        .filter(Credential.cloud_account_id == account.id, Credential.verified.is_(True))
        .order_by(Credential.display_order, Credential.id)
        .first()
    )
    if credential is None:
        return _deny("CLOUD_PERMISSION_DENIED")
    # permission_scope가 비어 있으면(예: 목업 데이터처럼 실제 검증을 거치지 않은 credential)
    # 아직 프로빙된 적이 없다는 뜻이라 이 검사를 건너뛴다 — 실제로 검증돼 채워진 경우에만
    # resource_control이 명시적으로 false면 거부한다.
    if credential.permission_scope and not credential.permission_scope.get("resource_control", False):
        return _deny("CLOUD_PERMISSION_DENIED")

    # 3) stale/deleted 상태가 동작을 허용하는가
    if resource.deleted_at is not None:
        return _deny("RESOURCE_ALREADY_DELETED")
    if resource.is_stale:
        return _deny("RESOURCE_STALE")

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        return _deny("PROVIDER_API_ERROR")

    try:
        perform_action(
            provider=account.provider,
            service_code=service.service_code,
            original_resource_type=resource.original_resource_type,
            action=action,
            secret_payload=secret_payload,
            external_account_id=account.external_account_id,
            region=resource.region,
            external_resource_id=resource.external_resource_id,
            force_empty=force_empty,
        )
    except ResourceActionError as exc:
        record_audit_event(
            db,
            actor_user_id=current_user.id,
            action=f"resource.{action}",
            target_type="resource",
            target_id=resource_id_str,
            result="failure",
            provider=account.provider,
            metadata={"error_code": exc.code},
            request_id=_request_id(request),
        )
        return _failed(resource_id_str, exc.code)
    finally:
        del secret_payload

    if action == "start":
        resource.status = "RUNNING"
    elif action == "stop":
        resource.status = "STOPPED"
    else:
        resource.status = "DELETED"
        resource.deleted_at = dt.datetime.now(dt.timezone.utc)

    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action=f"resource.{action}",
        target_type="resource",
        target_id=resource_id_str,
        result="success",
        provider=account.provider,
        request_id=_request_id(request),
    )
    return ActionResultItem(resource_id=resource_id_str, status="success", error=None)


@router.post("/resources/action", response_model=ResourceActionResponse)
def resource_action(
    payload: ResourceActionRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> ResourceActionResponse:
    if len(set(payload.resource_ids)) != len(payload.resource_ids):
        raise validation_error("resource_ids에 중복된 값이 있습니다.")

    # 일괄 요청은 항목별 부분 성공으로 처리한다(§19 미확정 항목 결정 — CLAUDE.md 기록).
    results = [
        _process_action_item(db, current_user, raw_id, payload.action, payload.force_empty, request)
        for raw_id in payload.resource_ids
    ]
    db.commit()
    return ResourceActionResponse(data=ResourceActionData(action=payload.action, results=results))
