from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_current_user, require_confirmation
from app.errors import ApiError, validation_error
from app.models import CloudAccount, Credential, Notification, ProvisioningJob, Resource, ServiceCatalog, User
from app.provisioning import get_runner
from app.providers import PROVIDERS
from app.schemas.provisioning import (
    CreateProvisioningJobRequest,
    ProvisioningJobCreateData,
    ProvisioningJobCreateResponse,
    ProvisioningJobError,
    ProvisioningJobListData,
    ProvisioningJobListResponse,
    ProvisioningJobOut,
    ProvisioningJobResponse,
)
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["provisioning"])

_CANCEL_REQUESTED: set[int] = set()

# credentials.py/audit.py/logging_config.py에 있는 것과 같은 종류의 민감 키 이름 목록이지만,
# 여기서는 "요청을 아예 거부"하는 입력 검증 목적이라 별도로 둔다(§10.3 SECRET_FIELD_NOT_ALLOWED).
_SECRET_FIELD_DENYLIST = {
    "secret",
    "secret_payload",
    "password",
    "access_key_id",
    "secret_access_key",
    "session_token",
    "client_secret",
    "private_key",
    "private_key_id",
    "token",
    "authorization",
    "api_key",
    "credentials",
}


def _find_secret_field(spec: dict[str, Any], prefix: str) -> str | None:
    for key, value in spec.items():
        if key.lower() in _SECRET_FIELD_DENYLIST:
            return f"{prefix}.{key}"
        if isinstance(value, dict):
            nested = _find_secret_field(value, f"{prefix}.{key}")
            if nested:
                return nested
    return None


# --- 소유권/조회 헬퍼 -----------------------------------------------------------------------


def _parse_id(raw: str, not_found_code: str, not_found_message: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(404, not_found_code, not_found_message) from exc


def _get_owned_credential(db: Session, user_id: int, credential_id: str) -> tuple[Credential, CloudAccount]:
    cred_id = _parse_id(credential_id, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
    row = (
        db.query(Credential, CloudAccount)
        .join(CloudAccount, Credential.cloud_account_id == CloudAccount.id)
        .filter(Credential.id == cred_id, CloudAccount.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
    return row


def _get_owned_job(db: Session, user_id: int, raw_id: str) -> ProvisioningJob:
    job = db.get(ProvisioningJob, _parse_id(raw_id, "PROVISIONING_JOB_NOT_FOUND", "프로비저닝 작업을 찾을 수 없습니다."))
    if job is None or job.user_id != user_id:
        raise ApiError(404, "PROVISIONING_JOB_NOT_FOUND", "프로비저닝 작업을 찾을 수 없습니다.")
    return job


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# --- 직렬화 ---------------------------------------------------------------------------------


def _serialize_job(job: ProvisioningJob) -> ProvisioningJobOut:
    spec = job.spec_json or {}
    error = ProvisioningJobError(code=job.error_code, message=job.error_message) if job.error_code else None
    return ProvisioningJobOut(
        id=str_id(job.id),
        credential_id=str_id(job.credential_id),
        service_catalog_id=str_id(job.service_catalog_id),
        workspace_name=job.workspace_name,
        common_spec=spec.get("common_spec", {}),
        provider_spec=spec.get("provider_spec", {}),
        status=job.status,
        progress_percent=job.progress_percent,
        created_resource_count=job.created_resource_count,
        result=job.result_json,
        error=error,
        created_at=iso_z(job.created_at),
        started_at=iso_z(job.started_at),
        finished_at=iso_z(job.finished_at),
    )


# --- 프로비저닝 실행(백그라운드) -------------------------------------------------------------


def _create_resource_from_job(
    db: Session, job: ProvisioningJob, service: ServiceCatalog, account: CloudAccount, credential: Credential, outputs: dict
) -> None:
    common_spec = (job.spec_json or {}).get("common_spec", {})
    instance_name = outputs.get("instance_name") or f"mcp-{common_spec.get('name')}"
    provider_resource_key = f"{service.provider}:{service.service_code}:{instance_name}"
    existing = (
        db.query(Resource).filter_by(cloud_account_id=account.id, provider_resource_key=provider_resource_key).one_or_none()
    )
    if existing is not None:
        return

    zone = outputs.get("zone")
    region = zone.rsplit("-", 1)[0] if zone else (job.spec_json or {}).get("provider_spec", {}).get("region")
    now = dt.datetime.now(dt.timezone.utc)
    db.add(
        Resource(
            cloud_account_id=account.id,
            service_catalog_id=service.id,
            first_collected_by_credential_id=credential.id,
            last_collected_by_credential_id=credential.id,
            provider_resource_key=provider_resource_key,
            external_resource_id=instance_name,
            original_resource_type="Compute Engine Instance",
            name=instance_name,
            region=region,
            status="RUNNING",
            tags={"managed-by": "multi-cloud-platform", "job-id": str(job.id)},
            raw_metadata=outputs,
            first_seen_at=now,
            last_seen_at=now,
            last_synced_at=now,
        )
    )
    job.created_resource_count = 1


def _create_notification(db: Session, job: ProvisioningJob, *, success: bool, resource_name: str | None) -> None:
    common_spec = (job.spec_json or {}).get("common_spec", {})
    resource = resource_name or common_spec.get("name") or job.workspace_name
    params: dict[str, Any] = {"resource": resource, "job_id": str(job.id)}
    if not success and job.error_message:
        params["reason"] = job.error_message
    db.add(
        Notification(
            user_id=job.user_id,
            type="provisioning_succeeded" if success else "provisioning_failed",
            reference_type="provisioning_job",
            reference_id=job.id,
            message_key="notif.provisioning.succeeded" if success else "notif.provisioning.failed",
            message_params=params,
        )
    )


def _finalize_job(db: Session, job: ProvisioningJob, service: ServiceCatalog | None) -> None:
    """job을 종결 상태로 커밋한다 — §14 "완료 시 알림을 만들고 감사 action `provisioning.complete`를
    기록한다"는 success/failed 모든 경로에 적용되므로, 조기 반환(unverified credential 등)에서도
    반드시 이 함수를 거치게 한다(처음엔 성공 경로에서만 호출해서 실제 서버로 확인해보니 조기 실패
    job엔 알림·감사 로그가 안 남는 버그가 있었다).
    """
    job.finished_at = dt.datetime.now(dt.timezone.utc)
    if job.status == "success":
        _create_notification(db, job, success=True, resource_name=(job.result_json or {}).get("instance_name"))
    elif job.status == "failed":
        _create_notification(db, job, success=False, resource_name=None)
    # cancelled는 §14 기본 기록 대상 표에 없어 알림을 만들지 않는다(sync_jobs와 동일한 정책).

    record_audit_event(
        db,
        actor_user_id=job.user_id,
        action="provisioning.complete",
        target_type="provisioning_job",
        target_id=str(job.id),
        result="success" if job.status == "success" else "failure",
        provider=service.provider if service else None,
        metadata={"job_id": job.id, "error_code": job.error_code} if job.error_code else {"job_id": job.id},
    )
    db.commit()


def _process_provisioning_job(db: Session, job: ProvisioningJob) -> None:
    """`_run_provisioning_job`이 `SessionLocal()`을 연 뒤 호출하는 실제 처리부.

    `app/routers/sync_jobs.py`의 `_run_sync_job`/`_process_sync_item` 분리와 같은 이유로
    나눴다 — 테스트가 `SessionLocal()`(테스트 트랜잭션과 무관한 별도 커넥션) 없이
    `db_session`을 직접 넘겨 호출할 수 있게 한다.
    """
    credential = db.get(Credential, job.credential_id)
    service = db.get(ServiceCatalog, job.service_catalog_id)
    account = db.get(CloudAccount, credential.cloud_account_id) if credential else None

    if credential is None or account is None or not credential.verified:
        job.status = "failed"
        job.error_code = "CLOUD_PERMISSION_DENIED"
        job.error_message = "검증된 자격 증명이 없습니다."
        _finalize_job(db, job, service)
        return

    # permission_scope가 비어 있으면(예: 검증을 거치지 않은 목업 credential) 아직 프로빙된 적이
    # 없다는 뜻이라 이 검사를 건너뛴다 — `app/routers/resources.py`와 같은 정책(CLAUDE.md 기록).
    if credential.permission_scope and not credential.permission_scope.get("provision", False):
        job.status = "failed"
        job.error_code = "CLOUD_PERMISSION_DENIED"
        job.error_message = "이 자격 증명에는 생성 권한이 없습니다."
        _finalize_job(db, job, service)
        return

    runner = get_runner(service.provider, service.service_code)
    if runner is None:
        # 요청 시점에 이미 501로 걸렀어야 하지만, 방어적으로 한 번 더 막는다.
        job.status = "failed"
        job.error_code = "TERRAFORM_ERROR"
        job.error_message = "지원하지 않는 프로비저닝 조합입니다."
        _finalize_job(db, job, service)
        return

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        job.status = "failed"
        job.error_code = "PROVIDER_API_ERROR"
        _finalize_job(db, job, service)
        return

    settings = get_settings()
    workspace_dir = Path(settings.terraform_workspaces_dir) / job.workspace_name
    spec = job.spec_json or {}
    try:
        result = runner.run(
            job_id=job.id,
            workspace_dir=workspace_dir,
            project_id=account.external_account_id,
            common_spec=spec.get("common_spec", {}),
            provider_spec=spec.get("provider_spec", {}),
            secret_payload=secret_payload,
            cancel_check=lambda: job.id in _CANCEL_REQUESTED,
        )
    finally:
        del secret_payload

    _CANCEL_REQUESTED.discard(job.id)
    db.refresh(job)

    if result.cancelled:
        job.status = "cancelled"
    elif result.success:
        job.status = "success"
        job.progress_percent = 100
        job.result_json = result.outputs or {}
        _create_resource_from_job(db, job, service, account, credential, result.outputs or {})
    else:
        job.status = "failed"
        job.error_code = result.error_code
        job.error_message = result.error_message

    _finalize_job(db, job, service)


def _run_provisioning_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(ProvisioningJob, job_id)
        if job is None:
            return

        if job_id in _CANCEL_REQUESTED:
            job.status = "cancelled"
            job.finished_at = dt.datetime.now(dt.timezone.utc)
            _CANCEL_REQUESTED.discard(job_id)
            db.commit()
            return

        job.status = "running"
        job.started_at = dt.datetime.now(dt.timezone.utc)
        db.commit()

        _process_provisioning_job(db, job)
    finally:
        db.close()


# --- 엔드포인트 --------------------------------------------------------------------------


@router.post("/provisioning/{provider}/{service}", response_model=ProvisioningJobCreateResponse, status_code=202)
def create_provisioning_job(
    provider: str,
    service: str,
    payload: CreateProvisioningJobRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> ProvisioningJobCreateResponse:
    if provider not in PROVIDERS:
        raise validation_error("지원하지 않는 provider입니다.", details=[{"field": "provider", "reason": "invalid"}])
    if not idempotency_key:
        raise ApiError(422, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key 헤더가 필요합니다.")

    service_row = db.query(ServiceCatalog).filter_by(provider=provider, service_code=service).one_or_none()
    if service_row is None:
        raise ApiError(404, "SERVICE_NOT_FOUND", "서비스를 찾을 수 없습니다.")
    if not service_row.provisionable:
        raise validation_error(
            "이 서비스는 생성할 수 없습니다.", details=[{"field": "service", "reason": "not_provisionable"}]
        )

    runner = get_runner(provider, service)
    if runner is None:
        raise ApiError(
            501,
            "PROVISIONING_NOT_IMPLEMENTED",
            "이 provider·서비스 조합의 프로비저닝은 아직 구현되지 않았습니다.",
        )

    secret_field = _find_secret_field(payload.common_spec, "common_spec") or _find_secret_field(
        payload.provider_spec, "provider_spec"
    )
    if secret_field:
        raise ApiError(422, "SECRET_FIELD_NOT_ALLOWED", f"{secret_field}에는 secret 값을 넣을 수 없습니다.")

    credential, account = _get_owned_credential(db, current_user.id, payload.credential_id)
    if account.provider != provider:
        raise validation_error(
            "credential의 provider가 요청 경로와 일치하지 않습니다.",
            details=[{"field": "credential_id", "reason": "provider_mismatch"}],
        )

    runner.validate_spec(payload.common_spec, payload.provider_spec)

    canonical_new = {
        "credential_id": payload.credential_id,
        "common_spec": payload.common_spec,
        "provider_spec": payload.provider_spec,
    }
    existing = (
        db.query(ProvisioningJob)
        .filter_by(user_id=current_user.id, idempotency_key=idempotency_key)
        .one_or_none()
    )
    if existing is not None:
        canonical_existing = {
            "credential_id": str_id(existing.credential_id),
            "common_spec": (existing.spec_json or {}).get("common_spec", {}),
            "provider_spec": (existing.spec_json or {}).get("provider_spec", {}),
        }
        if canonical_existing != canonical_new:
            raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 Idempotency-Key로 다른 요청을 보낼 수 없습니다.")
        return ProvisioningJobCreateResponse(
            data=ProvisioningJobCreateData(
                id=str_id(existing.id),
                status=existing.status,
                created_at=iso_z(existing.created_at),
                status_url=f"/api/v1/provisioning/jobs/{existing.id}",
            )
        )

    job = ProvisioningJob(
        user_id=current_user.id,
        credential_id=credential.id,
        service_catalog_id=service_row.id,
        workspace_name=f"pending-{idempotency_key}",
        idempotency_key=idempotency_key,
        spec_json={"common_spec": payload.common_spec, "provider_spec": payload.provider_spec},
        status="queued",
    )
    db.add(job)
    db.flush()
    job.workspace_name = f"user-{current_user.id}-job-{job.id}"
    db.flush()

    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="provisioning.request",
        target_type="provisioning_job",
        target_id=str(job.id),
        result="requested",
        provider=provider,
        metadata={"service_catalog_id": job.service_catalog_id},
        request_id=_request_id(request),
    )
    db.commit()
    db.refresh(job)

    background_tasks.add_task(_run_provisioning_job, job.id)

    return ProvisioningJobCreateResponse(
        data=ProvisioningJobCreateData(
            id=str_id(job.id),
            status=job.status,
            created_at=iso_z(job.created_at),
            status_url=f"/api/v1/provisioning/jobs/{job.id}",
        )
    )


@router.get("/provisioning/jobs", response_model=ProvisioningJobListResponse)
def list_provisioning_jobs(
    provider: str | None = None,
    service_catalog_id: str | None = None,
    status: str | None = None,
    credential_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProvisioningJobListResponse:
    query = db.query(ProvisioningJob).filter(ProvisioningJob.user_id == current_user.id)

    if provider is not None:
        if provider not in PROVIDERS:
            raise validation_error("지원하지 않는 provider입니다.", details=[{"field": "provider", "reason": "invalid"}])
        query = query.join(ServiceCatalog, ProvisioningJob.service_catalog_id == ServiceCatalog.id).filter(
            ServiceCatalog.provider == provider
        )
    if service_catalog_id is not None:
        query = query.filter(
            ProvisioningJob.service_catalog_id == _parse_id(service_catalog_id, "PROVISIONING_JOB_NOT_FOUND", "")
        )
    if status is not None:
        query = query.filter(ProvisioningJob.status == status)
    if credential_id is not None:
        query = query.filter(
            ProvisioningJob.credential_id == _parse_id(credential_id, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
        )

    jobs = query.order_by(ProvisioningJob.id.desc()).all()
    items = [_serialize_job(job) for job in jobs]
    return ProvisioningJobListResponse(data=ProvisioningJobListData(items=items, total=len(items)))


@router.get("/provisioning/jobs/{job_id}", response_model=ProvisioningJobResponse)
def get_provisioning_job(
    job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProvisioningJobResponse:
    job = _get_owned_job(db, current_user.id, job_id)
    return ProvisioningJobResponse(data=_serialize_job(job))


@router.post("/provisioning/jobs/{job_id}/cancel", response_model=ProvisioningJobResponse, status_code=202)
def cancel_provisioning_job(
    job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProvisioningJobResponse:
    job = _get_owned_job(db, current_user.id, job_id)
    if job.status not in ("queued", "running"):
        raise ApiError(409, "JOB_NOT_CANCELLABLE", "이미 종료된 작업은 취소할 수 없습니다.")

    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    else:
        _CANCEL_REQUESTED.add(job.id)

    db.refresh(job)
    return ProvisioningJobResponse(data=_serialize_job(job))
