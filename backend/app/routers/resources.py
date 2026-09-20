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
from app.providers import aws as aws_provider
from app.resource_actions import ResourceActionError, perform_action, supported_actions
from app.schemas.errors import explanation_for, specific_reason_for
from app.metrics import get_top_utilization, get_unused_resources
from app.schemas.resources import (
    ActionResultError,
    ActionResultItem,
    CliAccessData,
    CliAccessResponse,
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
    UnusedResourceItem,
    UnusedResourcesData,
    UnusedResourcesResponse,
    UtilizationData,
    UtilizationItem,
    UtilizationResponse,
)
from app.providers.session import CredentialResolutionError, resolve_secret_payload
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


@router.get("/resources/utilization/top", response_model=UtilizationResponse)
def resources_utilization_top(
    limit: int = Query(5, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UtilizationResponse:
    """CPU 사용률 상위 N개를 실시간으로 조회한다(보고서 §3.3 "리소스 사용률 상위" 섹션용,
    2026-09-17). 저장된 값이 아니라 이 요청을 처리하는 순간 CSP Monitoring API를 호출한
    결과다 — `as_of`가 그 시점이다. app/metrics.py 참고."""
    items = get_top_utilization(db, current_user, limit=limit)
    return UtilizationResponse(
        data=UtilizationData(
            items=[UtilizationItem(**item) for item in items],
            as_of=iso_z(dt.datetime.now(dt.timezone.utc)),
        )
    )


@router.get("/resources/unused/top", response_model=UnusedResourcesResponse)
def resources_unused_top(
    limit: int = Query(10, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UnusedResourcesResponse:
    """미연결 디스크·유휴 인스턴스를 실시간으로 조회한다(보고서 §3.3 "미사용 리소스" 섹션용,
    2026-09-19). 감지 범위·근거는 app/metrics.py::get_unused_resources 참고 — 미연결 공인 IP는
    아직 어떤 provider도 리소스로 수집하지 않아 이 응답에 포함되지 않는다."""
    items = get_unused_resources(db, current_user, limit=limit)
    return UnusedResourcesResponse(
        data=UnusedResourcesData(
            items=[UnusedResourceItem(**item) for item in items],
            as_of=iso_z(dt.datetime.now(dt.timezone.utc)),
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


# --- POST /resources/{id}/cli-access ----------------------------------------------------
# SSH 키 페어(정적 비밀키)를 새로 만드는 대신, STS GetSessionToken으로 짧게 만료되는 임시
# AWS CLI 자격증명을 발급해 SSM Session Manager로 접속하게 한다(2026-09-15 결정 — 마이페이지
# 크리덴셜을 IAM Role/MFA로 옮기려는 방향과 같은 원칙). 인스턴스에 SSM 접속 권한을 주는 IAM
# 인스턴스 프로파일은 `terraform/aws/ec2/main.tf`(aws_iam_instance_profile.ssm)가 붙인다.


@router.post("/resources/{resource_id}/cli-access", response_model=CliAccessResponse)
def issue_resource_cli_access(
    resource_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> CliAccessResponse:
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

    # 지금은 AWS EC2 인스턴스만 SSM 인스턴스 프로파일이 붙어 있어 지원 대상이다.
    if account.provider != "aws" or service.service_code != "ec2" or resource.original_resource_type == "EBS Volume":
        raise ApiError(422, "UNSUPPORTED_OPERATION", "이 리소스는 AWS CLI 접속을 지원하지 않습니다.")
    if resource.deleted_at is not None:
        raise ApiError(409, "RESOURCE_ALREADY_DELETED", "삭제된 리소스입니다.")

    credential = (
        db.query(Credential)
        .filter(Credential.cloud_account_id == account.id, Credential.verified.is_(True))
        .order_by(Credential.display_order, Credential.id)
        .first()
    )
    if credential is None:
        raise ApiError(422, "CLOUD_PERMISSION_DENIED", "검증된 자격 증명이 없습니다 — 마이페이지에서 검증하세요.")

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        raise ApiError(422, "PROVIDER_API_ERROR", "자격 증명을 복호화하지 못했습니다.")

    try:
        session = aws_provider.issue_cli_session(secret_payload)
    except ResourceActionError as exc:
        # 위임 credential은 실패 사유가 사용자가 고칠 수 있는 것(신뢰 정책/ExternalId)일 수
        # 있어 502로 뭉개지 않는다. STS는 원인을 구분해주지 않으므로 점검 항목을 함께 준다.
        if exc.code == "CLOUD_PERMISSION_DENIED":
            raise ApiError(
                422,
                exc.code,
                "역할을 빌릴 수 없어 CLI 자격 증명을 발급하지 못했습니다. 역할 이름·신뢰 정책의 "
                "계정 ID·External ID를 확인해 주세요.",
            ) from exc
        if exc.code == "CREDENTIAL_VERIFICATION_FAILED":
            raise ApiError(
                422, exc.code, "자격 증명에 역할 정보가 없습니다. 마이페이지에서 다시 등록해 주세요."
            ) from exc
        raise ApiError(502, "PROVIDER_API_ERROR", "AWS에서 임시 자격 증명을 발급받지 못했습니다.") from exc
    finally:
        del secret_payload

    region = resource.region or "ap-northeast-2"
    command = (
        f"export AWS_ACCESS_KEY_ID={session['access_key_id']}\n"
        f"export AWS_SECRET_ACCESS_KEY={session['secret_access_key']}\n"
        f"export AWS_SESSION_TOKEN={session['session_token']}\n"
        f"aws ssm start-session --target {resource.external_resource_id} --region {region}"
    )

    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="resource.cli_access",
        target_type="resource",
        target_id=str_id(resource.id),
        result="success",
        provider=account.provider,
        request_id=_request_id(request),
    )
    db.commit()

    return CliAccessResponse(
        data=CliAccessData(
            access_key_id=session["access_key_id"],
            secret_access_key=session["secret_access_key"],
            session_token=session["session_token"],
            expires_at=iso_z(session["expires_at"]),
            region=region,
            instance_id=resource.external_resource_id,
            command=command,
        )
    )


# --- POST /resources/action -------------------------------------------------------------


def _action_error(code: str, message: str | None = None) -> ActionResultError:
    # 사전 검사 실패는 code만(message=None) → 카탈로그 고정 설명. perform_action 실패는 SDK 원문
    # (message)까지 있어 구체 원인 번역을 시도한다.
    return ActionResultError(
        code=code,
        message=message,
        explanation=explanation_for(code),
        specific_reason=specific_reason_for(code, message),
    )


def _rejected(resource_id: str, code: str, message: str | None = None) -> ActionResultItem:
    return ActionResultItem(resource_id=resource_id, status="rejected", error=_action_error(code, message))


def _failed(resource_id: str, code: str, message: str | None = None) -> ActionResultItem:
    return ActionResultItem(resource_id=resource_id, status="failed", error=_action_error(code, message))


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
    # resource_control 권한은 여기서 사전 차단하지 않는다 — `permission_scope.resource_control`은
    # AWS의 경우 iam:SimulatePrincipalPolicy로 프로빙하는데, EC2 권한만 있는 키(IAM 권한 없음)는
    # 시뮬레이션 자체가 실패해 실제로는 제어 가능한데도 false로 잘못 기록된다(false negative).
    # 실제 권한 게이트는 아래 perform_action의 CSP SDK 호출로 둔다 — 진짜 권한이 없으면 그 호출이
    # 실패하고 PROVIDER_API_ERROR로 반환된다(프로비저닝의 Terraform apply 게이트와 같은 정책).

    # 3) stale/deleted 상태가 동작을 허용하는가
    if resource.deleted_at is not None:
        return _deny("RESOURCE_ALREADY_DELETED")
    if resource.is_stale:
        return _deny("RESOURCE_STALE")

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        return _deny("PROVIDER_API_ERROR")

    # 위임 credential이면 임시 자격증명을 발급받는다(레거시는 그대로 통과). 일괄 요청은
    # 항목별 부분 성공이므로, 실패해도 나머지 리소스 처리는 계속된다.
    try:
        secret_payload = resolve_secret_payload(
            account.provider, secret_payload, credential_id=credential.id
        )
    except CredentialResolutionError as exc:
        return _deny(exc.error_code)

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
        return _failed(resource_id_str, exc.code, exc.message)
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
