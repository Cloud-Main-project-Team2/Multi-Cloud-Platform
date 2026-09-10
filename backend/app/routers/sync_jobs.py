"""API 명세서 v1.1 §9 리소스 동기화 API.

이 서버엔 별도 워커/큐 프로세스가 없다 — `POST /sync-jobs`는 FastAPI `BackgroundTasks`로
응답을 먼저 돌려주고 같은 프로세스 안에서 백그라운드로 CSP 조회를 실행한다(단일 프로세스
전제 — replica를 늘리면 별도 워커로 분리해야 한다, `docker-compose.yml`의 기존 alembic
1-replica 전제와 같은 종류의 제약).

취소(`POST /sync-jobs/{id}/cancel`)는 best-effort다: 실행 중인 job은 프로세스 메모리의
`_CANCEL_REQUESTED` 집합에 표시만 해 두고, 다음 계정 항목으로 넘어가기 전에 그 집합을 확인해
멈춘다. 이미 시작된 항목 하나는 끝까지 진행된다(§9.4와 동일한 정도의 best-effort). 이 집합은
프로세스 재시작 시 사라진다 — 별도 영속 취소 플래그 컬럼은 이번 세션 범위 밖.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.models import CloudAccount, Credential, Resource, ResourceSyncJob, ResourceSyncJobItem, ServiceCatalog, User
from app.resource_sync import DiscoveredResource, SyncError, discover_resources
from app.schemas.sync_jobs import (
    ProviderSummary,
    SyncJobCreateData,
    SyncJobCreateRequest,
    SyncJobCreateResponse,
    SyncJobDetail,
    SyncJobDetailResponse,
    SyncJobItemError,
    SyncJobItemOut,
    SyncJobListData,
    SyncJobListResponse,
)
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["sync-jobs"])

_CANCEL_REQUESTED: set[int] = set()
_TERMINAL_STATUSES = {"success", "failed", "cancelled"}


def _parse_id(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(404, "SYNC_JOB_NOT_FOUND", "동기화 작업을 찾을 수 없습니다.") from exc


def _parse_int_list(values: list[str]) -> list[int]:
    try:
        return [int(v) for v in values]
    except ValueError as exc:
        raise validation_error(
            "cloud_account_ids는 숫자 ID여야 합니다.", details=[{"field": "cloud_account_ids", "reason": "invalid"}]
        ) from exc


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "success"
    if any(s == "running" for s in statuses):
        return "running"
    if all(s == "pending" for s in statuses):
        return "pending"
    if any(s == "pending" for s in statuses):
        return "running"
    if all(s == "cancelled" for s in statuses):
        return "cancelled"
    has_failed = any(s == "failed" for s in statuses)
    has_success = any(s == "success" for s in statuses)
    if has_failed and has_success:
        return "partial_success"
    if has_failed:
        return "failed"
    return "success"


# --- 조회 헬퍼 --------------------------------------------------------------------------


def _get_owned_job(db: Session, user_id: int, raw_id: str) -> ResourceSyncJob:
    job_id = _parse_id(raw_id)
    job = db.get(ResourceSyncJob, job_id)
    if job is None or job.user_id != user_id:
        raise ApiError(404, "SYNC_JOB_NOT_FOUND", "동기화 작업을 찾을 수 없습니다.")
    return job


def _serialize_item(item: ResourceSyncJobItem) -> SyncJobItemOut:
    error = SyncJobItemError(code=item.error_code, message=item.error_message) if item.error_code else None
    return SyncJobItemOut(
        id=str_id(item.id),
        cloud_account_id=str_id(item.cloud_account_id),
        credential_id=str_id(item.credential_id),
        provider=item.provider,
        status=item.status,
        resources_discovered=item.resources_discovered,
        resources_created=item.resources_created,
        resources_updated=item.resources_updated,
        resources_marked_stale=item.resources_marked_stale,
        error=error,
        started_at=iso_z(item.started_at),
        finished_at=iso_z(item.finished_at),
    )


def _build_job_detail(db: Session, job: ResourceSyncJob) -> SyncJobDetail:
    items = db.query(ResourceSyncJobItem).filter_by(sync_job_id=job.id).order_by(ResourceSyncJobItem.id).all()
    by_provider: dict[str, list[str]] = {}
    for item in items:
        by_provider.setdefault(item.provider, []).append(item.status)
    provider_summary = [
        ProviderSummary(
            provider=p,
            status=_aggregate_status(statuses),
            completed=sum(1 for s in statuses if s in _TERMINAL_STATUSES),
            total=len(statuses),
        )
        for p, statuses in sorted(by_provider.items())
    ]
    return SyncJobDetail(
        id=str_id(job.id),
        status=job.status,
        requested_at=iso_z(job.requested_at),
        started_at=iso_z(job.started_at),
        finished_at=iso_z(job.finished_at),
        provider_summary=provider_summary,
        items=[_serialize_item(i) for i in items],
    )


# --- 동기화 실행(백그라운드) ---------------------------------------------------------------


def _upsert_discovered_resources(
    db: Session, account: CloudAccount, credential: Credential, discovered: list[DiscoveredResource]
) -> tuple[int, int, set[str]]:
    now = dt.datetime.now(dt.timezone.utc)
    created = 0
    updated = 0
    seen_keys: set[str] = set()
    service_cache: dict[str, ServiceCatalog | None] = {}

    for disc in discovered:
        service = service_cache.get(disc.service_code, "_unset")
        if service == "_unset":
            service = (
                db.query(ServiceCatalog)
                .filter_by(provider=account.provider, service_code=disc.service_code)
                .one_or_none()
            )
            service_cache[disc.service_code] = service
        if service is None:
            continue  # service_catalog에 없는 service_code — 스킵(있을 수 없지만 방어적으로)

        provider_resource_key = f"{account.provider}:{disc.service_code}:{disc.external_resource_id}"
        seen_keys.add(provider_resource_key)
        existing = (
            db.query(Resource)
            .filter_by(cloud_account_id=account.id, provider_resource_key=provider_resource_key)
            .one_or_none()
        )
        if existing is None:
            db.add(
                Resource(
                    cloud_account_id=account.id,
                    service_catalog_id=service.id,
                    first_collected_by_credential_id=credential.id,
                    last_collected_by_credential_id=credential.id,
                    provider_resource_key=provider_resource_key,
                    external_resource_id=disc.external_resource_id,
                    original_resource_type=disc.original_resource_type,
                    name=disc.name,
                    region=disc.region,
                    status=disc.status,
                    tags=disc.tags,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_synced_at=now,
                )
            )
            created += 1
        else:
            existing.last_collected_by_credential_id = credential.id
            existing.name = disc.name
            existing.region = disc.region
            existing.status = disc.status
            existing.tags = disc.tags
            existing.last_seen_at = now
            existing.last_synced_at = now
            existing.is_stale = False
            updated += 1

    db.flush()
    return created, updated, seen_keys


def _mark_stale_resources(db: Session, account: CloudAccount, seen_keys: set[str]) -> int:
    marked = 0
    candidates = (
        db.query(Resource)
        .filter(Resource.cloud_account_id == account.id, Resource.deleted_at.is_(None), Resource.is_stale.is_(False))
        .all()
    )
    for resource in candidates:
        if resource.provider_resource_key not in seen_keys:
            resource.is_stale = True
            marked += 1
    return marked


def _process_sync_item(db: Session, item: ResourceSyncJobItem, account: CloudAccount) -> None:
    item.status = "running"
    item.started_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    credential = db.get(Credential, item.credential_id) if item.credential_id else None
    if credential is None or not credential.verified:
        item.status = "failed"
        item.error_code = "CLOUD_PERMISSION_DENIED"
        item.error_message = "검증된 자격 증명이 없습니다."
        item.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        return

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        item.status = "failed"
        item.error_code = "PROVIDER_API_ERROR"
        item.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        return

    try:
        discovered = discover_resources(account.provider, secret_payload, account.external_account_id)
    except SyncError as exc:
        item.status = "failed"
        item.error_code = exc.code
        item.error_message = exc.message or None
        item.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        return
    finally:
        del secret_payload

    created, updated, seen_keys = _upsert_discovered_resources(db, account, credential, discovered)
    marked_stale = _mark_stale_resources(db, account, seen_keys)

    item.status = "success"
    item.resources_discovered = len(discovered)
    item.resources_created = created
    item.resources_updated = updated
    item.resources_marked_stale = marked_stale
    item.finished_at = dt.datetime.now(dt.timezone.utc)
    db.commit()


def _run_sync_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(ResourceSyncJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = dt.datetime.now(dt.timezone.utc)
        db.commit()

        items = db.query(ResourceSyncJobItem).filter_by(sync_job_id=job_id).order_by(ResourceSyncJobItem.id).all()
        for item in items:
            if job_id in _CANCEL_REQUESTED:
                item.status = "cancelled"
                db.commit()
                continue
            account = db.get(CloudAccount, item.cloud_account_id)
            _process_sync_item(db, item, account)

        db.refresh(job)
        if job_id in _CANCEL_REQUESTED:
            job.status = "cancelled"
            _CANCEL_REQUESTED.discard(job_id)
        else:
            job.status = _aggregate_status([i.status for i in items])
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    finally:
        db.close()


# --- 엔드포인트 --------------------------------------------------------------------------


@router.post("/sync-jobs", response_model=SyncJobCreateResponse, status_code=202)
def create_sync_job(
    payload: SyncJobCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncJobCreateResponse:
    existing = (
        db.query(ResourceSyncJob)
        .filter(ResourceSyncJob.user_id == current_user.id, ResourceSyncJob.status.in_(["pending", "running"]))
        .first()
    )
    if existing is not None:
        raise ApiError(409, "JOB_ALREADY_RUNNING", "이미 진행 중인 동기화 작업이 있습니다.")

    if payload.cloud_account_ids is None:
        accounts = (
            db.query(CloudAccount)
            .join(Credential, Credential.cloud_account_id == CloudAccount.id)
            .filter(CloudAccount.user_id == current_user.id, Credential.verified.is_(True))
            .distinct()
            .all()
        )
    else:
        ids = _parse_int_list(payload.cloud_account_ids)
        accounts = (
            db.query(CloudAccount)
            .filter(CloudAccount.id.in_(ids), CloudAccount.user_id == current_user.id)
            .all()
        )
        if len(accounts) != len(set(ids)):
            raise ApiError(404, "CLOUD_ACCOUNT_NOT_FOUND", "클라우드 계정을 찾을 수 없습니다.")

    if not accounts:
        raise validation_error("동기화할 검증된 클라우드 계정이 없습니다.")

    now = dt.datetime.now(dt.timezone.utc)
    job = ResourceSyncJob(user_id=current_user.id, status="pending", requested_at=now)
    db.add(job)
    db.flush()

    for account in accounts:
        credential = (
            db.query(Credential)
            .filter(Credential.cloud_account_id == account.id, Credential.verified.is_(True))
            .order_by(Credential.display_order, Credential.id)
            .first()
        )
        db.add(
            ResourceSyncJobItem(
                sync_job_id=job.id,
                cloud_account_id=account.id,
                credential_id=credential.id if credential else None,
                provider=account.provider,
                status="pending",
            )
        )
    db.commit()
    db.refresh(job)

    background_tasks.add_task(_run_sync_job, job.id)

    return SyncJobCreateResponse(
        data=SyncJobCreateData(
            id=str_id(job.id),
            status=job.status,
            requested_at=iso_z(job.requested_at),
            status_url=f"/api/v1/sync-jobs/{job.id}",
        )
    )


@router.get("/sync-jobs", response_model=SyncJobListResponse)
def list_sync_jobs(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> SyncJobListResponse:
    jobs = db.query(ResourceSyncJob).filter_by(user_id=current_user.id).order_by(ResourceSyncJob.id.desc()).all()
    items = [_build_job_detail(db, job) for job in jobs]
    return SyncJobListResponse(data=SyncJobListData(items=items, total=len(items)))


@router.get("/sync-jobs/{sync_job_id}", response_model=SyncJobDetailResponse)
def get_sync_job(
    sync_job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SyncJobDetailResponse:
    job = _get_owned_job(db, current_user.id, sync_job_id)
    return SyncJobDetailResponse(data=_build_job_detail(db, job))


@router.post("/sync-jobs/{sync_job_id}/cancel", response_model=SyncJobDetailResponse, status_code=202)
def cancel_sync_job(
    sync_job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SyncJobDetailResponse:
    job = _get_owned_job(db, current_user.id, sync_job_id)
    if job.status not in ("pending", "running"):
        raise ApiError(409, "JOB_NOT_CANCELLABLE", "이미 종료된 작업은 취소할 수 없습니다.")

    if job.status == "pending":
        job.status = "cancelled"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.query(ResourceSyncJobItem).filter_by(sync_job_id=job.id, status="pending").update({"status": "cancelled"})
        db.commit()
    else:
        _CANCEL_REQUESTED.add(job.id)

    db.refresh(job)
    return SyncJobDetailResponse(data=_build_job_detail(db, job))
