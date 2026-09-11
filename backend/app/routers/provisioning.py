"""API 명세서 v1.1 §10 프로비저닝 API.

**이번 세션 범위(2026-09-11, `jongkuk/aws-provisioning`, CLAUDE.md 기록)**: job 생성·조회·취소
4개 엔드포인트만 구현한다(`POST /provisioning/{provider}/{service}`, `GET /provisioning/jobs`,
`GET /provisioning/jobs/{job_id}`, `POST /provisioning/jobs/{job_id}/cancel`).
`POST /provisioning/price-comparisons`, `GET /provisioning/options`, `GET /service-catalog`는
순수 조회/추정 엔드포인트라 job 실행 파이프라인과 무관해 범위 밖으로 뺐다.

`app/provisioning.py`의 `get_runner()` 레지스트리에는 AWS EC2(`app/aws_provisioning.py`) 러너
하나만 실제로 붙어 있다 — `service_catalog`엔 12개 조합이 `provisionable=true`로 있지만, 러너가
없는 조합은 `501 PROVISIONING_NOT_IMPLEMENTED`로 명확히 응답한다.

이 서버엔 별도 워커/큐가 없다 — `app/routers/sync_jobs.py`와 같은 전제로 FastAPI
`BackgroundTasks` 안에서 같은 프로세스가 terraform CLI를 subprocess로 직접 구동한다(단일
프로세스 전제, replica를 늘리면 별도 워커로 분리해야 한다). 취소도 sync_jobs와 동일하게
best-effort·비영속이다: `_CANCEL_REQUESTED` 집합은 프로세스 메모리에만 있고,
`terraform_runner`가 init/plan/apply 사이사이에 그 값을 확인해 멈춘다 — 이미 시작된 terraform
호출 하나는 끝까지 진행된다.

**생성된 리소스를 인벤토리에 즉시 반영**: job이 성공하면 terraform output으로 `resources`
테이블에 행을 하나 만든다(다음 `POST /sync-jobs` 없이도 INV-01에 바로 보이도록). 이 매핑은
현재 AWS EC2 전용이다(`app/aws_provisioning.py`의 `outputs.tf` 키 이름에 의존) — 다른 러너를
추가할 때는 `_create_resource_from_job()`도 함께 확장해야 한다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_current_user, require_confirmation
from app.errors import ApiError
from app.models import CloudAccount, Credential, Notification, ProvisioningJob, Resource, ServiceCatalog, User
from app.provisioning import get_runner
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


def _parse_int(raw: str, code: str, message: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(404, code, message) from exc


def _get_owned_credential(db: Session, user_id: int, credential_id: str) -> tuple[Credential, CloudAccount]:
    cred_id = _parse_int(credential_id, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
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
    job = db.get(ProvisioningJob, _parse_int(raw_id, "PROVISIONING_JOB_NOT_FOUND", "프로비저닝 작업을 찾을 수 없습니다."))
    if job is None or job.user_id != user_id:
        raise ApiError(404, "PROVISIONING_JOB_NOT_FOUND", "프로비저닝 작업을 찾을 수 없습니다.")
    return job


def _serialize_job(job: ProvisioningJob) -> ProvisioningJobOut:
    error = ProvisioningJobError(code=job.error_code, message=job.error_message) if job.error_code else None
    spec = job.spec_json or {}
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


# --- 생성 실행(백그라운드) ------------------------------------------------------------------


def _create_resource_from_job(
    db: Session, job: ProvisioningJob, service_catalog: ServiceCatalog, credential: Credential
) -> None:
    external_resource_id = (job.result_json or {}).get("instance_id")
    if not external_resource_id:
        return

    cloud_account = db.get(CloudAccount, credential.cloud_account_id)
    provider_resource_key = f"{service_catalog.provider}:{service_catalog.service_code}:{external_resource_id}"
    existing = (
        db.query(Resource)
        .filter_by(cloud_account_id=cloud_account.id, provider_resource_key=provider_resource_key)
        .one_or_none()
    )

    now = dt.datetime.now(dt.timezone.utc)
    spec = job.spec_json or {}
    name = spec.get("common_spec", {}).get("name")
    region = spec.get("provider_spec", {}).get("region")

    if existing is None:
        db.add(
            Resource(
                cloud_account_id=cloud_account.id,
                service_catalog_id=service_catalog.id,
                first_collected_by_credential_id=credential.id,
                last_collected_by_credential_id=credential.id,
                provider_resource_key=provider_resource_key,
                external_resource_id=external_resource_id,
                original_resource_type="AWS::EC2::Instance",
                name=name,
                region=region,
                status="running",
                tags={},
                first_seen_at=now,
                last_seen_at=now,
                last_synced_at=now,
            )
        )
    else:
        existing.last_collected_by_credential_id = credential.id
        existing.status = "running"
        existing.last_seen_at = now
        existing.last_synced_at = now
        existing.is_stale = False
    db.flush()


def _run_provisioning_job(job_id: int) -> None:
    """실제 백그라운드 진입점. `SessionLocal()`은 테스트 트랜잭션과 무관한 별도 커넥션이라
    HTTP 계층 테스트에서는 이 함수 자체를 monkeypatch로 no-op화한다 — 실행 로직 검증은
    `_execute_job()`을 `db_session`으로 직접 호출해서 한다(test_sync_jobs_api.py의
    `_process_sync_item` 테스트와 같은 패턴)."""
    db = SessionLocal()
    try:
        job = db.get(ProvisioningJob, job_id)
        if job is None:
            return
        _execute_job(db, job)
    finally:
        db.close()


def _finish_job(db: Session, job: ProvisioningJob, service_catalog: ServiceCatalog) -> None:
    """모든 종결 경로(성공/실패/취소)가 거쳐가는 공통 마무리 — §10.5 "완료 시 알림을 만들고
    감사 action provisioning.complete를 기록한다"를 어느 실패 경로에서도 빠뜨리지 않기 위함."""
    job.finished_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    record_audit_event(
        db,
        actor_user_id=job.user_id,
        action="provisioning.complete",
        target_type="provisioning_job",
        target_id=str(job.id),
        result="success" if job.status == "success" else "failure",
        provider=service_catalog.provider,
        metadata={"workspace_name": job.workspace_name, "status": job.status},
    )
    db.add(
        Notification(
            user_id=job.user_id,
            type="provisioning",
            reference_type="provisioning_job",
            reference_id=job.id,
            message_key=f"provisioning.{job.status}",
            message_params={"workspace_name": job.workspace_name},
        )
    )
    db.commit()


def _execute_job(db: Session, job: ProvisioningJob) -> None:
    job_id = job.id
    if job_id in _CANCEL_REQUESTED:
        job.status = "cancelled"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        _CANCEL_REQUESTED.discard(job_id)
        db.commit()
        return

    service_catalog = db.get(ServiceCatalog, job.service_catalog_id)
    credential = db.get(Credential, job.credential_id)
    runner = get_runner(service_catalog.provider, service_catalog.service_code)

    job.status = "running"
    job.started_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    # 검증된 credential과 provision 권한 확인 — app/routers/resources.py의 resource_control
    # 검사와 같은 정책: permission_scope가 비어 있으면(아직 프로빙 안 된 목업 등) 건너뛴다.
    if not credential.verified:
        job.status = "failed"
        job.error_code = "CLOUD_PERMISSION_DENIED"
        job.error_message = "검증된 자격 증명이 없습니다."
        _finish_job(db, job, service_catalog)
        return
    if credential.permission_scope and not credential.permission_scope.get("provision", False):
        job.status = "failed"
        job.error_code = "CLOUD_PERMISSION_DENIED"
        job.error_message = "이 자격 증명에는 프로비저닝 권한이 없습니다."
        _finish_job(db, job, service_catalog)
        return

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        job.status = "failed"
        job.error_code = "PROVIDER_API_ERROR"
        job.error_message = "자격 증명을 복호화하지 못했습니다."
        _finish_job(db, job, service_catalog)
        return

    spec = job.spec_json or {}
    workspace_dir = Path(get_settings().terraform_workspaces_dir) / job.workspace_name
    try:
        result = runner.run(
            job_id=job.id,
            workspace_dir=workspace_dir,
            common_spec=spec.get("common_spec", {}),
            provider_spec=spec.get("provider_spec", {}),
            secret_payload=secret_payload,
            cancel_check=lambda: job_id in _CANCEL_REQUESTED,
        )
    finally:
        del secret_payload

    if result.cancelled or job_id in _CANCEL_REQUESTED:
        job.status = "cancelled"
        _CANCEL_REQUESTED.discard(job_id)
    elif result.success:
        job.status = "success"
        job.progress_percent = 100
        job.created_resource_count = 1
        job.terraform_state_ref = str(workspace_dir)
        job.result_json = result.outputs or {}
        _create_resource_from_job(db, job, service_catalog, credential)
    else:
        job.status = "failed"
        job.error_code = result.error_code
        job.error_message = result.error_message

    _finish_job(db, job, service_catalog)


# --- 엔드포인트 --------------------------------------------------------------------------


@router.post("/provisioning/{provider}/{service}", response_model=ProvisioningJobCreateResponse, status_code=202)
def create_provisioning_job(
    provider: str,
    service: str,
    payload: CreateProvisioningJobRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _confirmed: None = Depends(require_confirmation),
) -> ProvisioningJobCreateResponse:
    if not idempotency_key:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key 헤더가 필요합니다.")

    service_catalog = db.query(ServiceCatalog).filter_by(provider=provider, service_code=service).one_or_none()
    if service_catalog is None:
        raise ApiError(404, "SERVICE_NOT_FOUND", "서비스를 찾을 수 없습니다.")
    if not service_catalog.provisionable:
        raise ApiError(422, "RESOURCE_NOT_PROVISIONABLE", "이 서비스는 프로비저닝할 수 없습니다.")

    runner = get_runner(provider, service)
    if runner is None:
        raise ApiError(501, "PROVISIONING_NOT_IMPLEMENTED", "이 provider/service 조합은 아직 지원하지 않습니다.")

    offending = _find_secret_field(payload.common_spec, "common_spec") or _find_secret_field(
        payload.provider_spec, "provider_spec"
    )
    if offending:
        raise ApiError(
            422,
            "SECRET_FIELD_NOT_ALLOWED",
            "spec에 자격 증명으로 의심되는 필드가 포함되어 있습니다.",
            details=[{"field": offending, "reason": "secret_like_field"}],
        )

    credential, cloud_account = _get_owned_credential(db, current_user.id, payload.credential_id)
    if cloud_account.provider != provider:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")

    runner.validate_spec(payload.common_spec, payload.provider_spec)

    spec_json = {"common_spec": payload.common_spec, "provider_spec": payload.provider_spec}

    existing = (
        db.query(ProvisioningJob)
        .filter_by(user_id=current_user.id, idempotency_key=idempotency_key)
        .one_or_none()
    )
    if existing is not None:
        same_payload = (
            existing.spec_json == spec_json
            and existing.credential_id == credential.id
            and existing.service_catalog_id == service_catalog.id
        )
        if not same_payload:
            raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 Idempotency-Key로 다른 내용의 요청이 이미 존재합니다.")
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
        service_catalog_id=service_catalog.id,
        workspace_name="pending",
        idempotency_key=idempotency_key,
        spec_json=spec_json,
        status="queued",
    )
    db.add(job)
    db.flush()
    job.workspace_name = f"user-{current_user.id}-job-{job.id}"
    db.commit()
    db.refresh(job)

    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="provisioning.request",
        target_type="provisioning_job",
        target_id=str(job.id),
        result="requested",
        provider=provider,
        metadata={"workspace_name": job.workspace_name},
    )
    db.commit()

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
    if status:
        query = query.filter(ProvisioningJob.status == status)
    if credential_id:
        query = query.filter(
            ProvisioningJob.credential_id == _parse_int(credential_id, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
        )
    if service_catalog_id:
        query = query.filter(
            ProvisioningJob.service_catalog_id
            == _parse_int(service_catalog_id, "SERVICE_NOT_FOUND", "서비스를 찾을 수 없습니다.")
        )
    if provider:
        query = query.join(ServiceCatalog, ServiceCatalog.id == ProvisioningJob.service_catalog_id).filter(
            ServiceCatalog.provider == provider
        )

    jobs = query.order_by(ProvisioningJob.id.desc()).all()
    items = [_serialize_job(j) for j in jobs]
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
