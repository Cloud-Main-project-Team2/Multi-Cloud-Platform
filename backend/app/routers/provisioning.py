"""프로비저닝 생성 API — `docs/01_API_명세서_v1.1.md` 10절.

현재 실제로 생성 가능한 조합은 `(azure, vm)` 하나뿐이다(다른 provider/service는
`app/services/provisioning/registry.py`에 실행기가 추가되면 자동으로 지원된다 —
이 라우터 자체는 provider/service에 대해 제네릭하다).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError
from app.models import AuditEvent, CloudAccount, Credential, ProvisioningJob, ServiceCatalog
from app.schemas.provisioning import ProvisioningCreateRequest
from app.security.auth import get_current_user_id
from app.services.provisioning.registry import EXECUTORS
from app.services.provisioning.validation import find_secret_field

router = APIRouter(prefix="/api/v1", tags=["provisioning"])


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


def _job_created_response(job: ProvisioningJob) -> dict:
    return {
        "data": {
            "id": str(job.id),
            "status": job.status,
            "created_at": _iso(job.created_at),
            "status_url": f"/api/v1/provisioning/jobs/{job.id}",
        }
    }


@router.post("/provisioning/{provider}/{service}", status_code=202)
def create_provisioning_job(
    provider: str,
    service: str,
    body: ProvisioningCreateRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    action_confirmed: str | None = Header(default=None, alias="X-Action-Confirmed"),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    if not idempotency_key:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key 헤더가 필요합니다.")
    if action_confirmed != "true":
        raise ApiError(428, "CONFIRMATION_REQUIRED", "X-Action-Confirmed: true 헤더가 필요합니다.")

    catalog_row = db.execute(
        select(ServiceCatalog).where(ServiceCatalog.provider == provider, ServiceCatalog.service_code == service)
    ).scalar_one_or_none()
    if catalog_row is None:
        raise ApiError(404, "SERVICE_NOT_FOUND", "요청한 provider/service 조합을 찾을 수 없습니다.")
    if not catalog_row.provisionable:
        raise ApiError(422, "RESOURCE_NOT_PROVISIONABLE", "이 서비스는 프로비저닝을 지원하지 않습니다.")

    executor = EXECUTORS.get((provider, service))
    if executor is None:
        raise ApiError(422, "RESOURCE_NOT_PROVISIONABLE", "이 provider/service의 생성 실행기는 아직 구현되지 않았습니다.")

    # provider_spec 중 일부(예: azure/vm의 admin_password)는 CSP 계정 자격증명이 아니라
    # 리소스 자체의 값이라 실행기가 명시적으로 예외 처리한다. common_spec에는 이런 예외가
    # 없다 — 거기엔 애초에 이런 값이 들어올 이유가 없다.
    sensitive_allow: frozenset[str] = getattr(executor, "SENSITIVE_PROVIDER_SPEC_FIELDS", frozenset())
    secret_field = find_secret_field(body.common_spec) or find_secret_field(body.provider_spec, allow=sensitive_allow)
    if secret_field is not None:
        raise ApiError(
            422,
            "SECRET_FIELD_NOT_ALLOWED",
            f"'{secret_field}' 필드는 common_spec/provider_spec에 넣을 수 없습니다.",
        )

    try:
        executor.ProviderSpec.model_validate(body.provider_spec)
    except ValidationError as exc:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "provider_spec이 유효하지 않습니다.",
            details=[{"field": ".".join(str(p) for p in e["loc"]), "reason": e["type"]} for e in exc.errors()],
        ) from exc

    common_name = body.common_spec.get("name")
    if not common_name or not isinstance(common_name, str):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "common_spec.name은 필수입니다.",
            details=[{"field": "common_spec.name", "reason": "required"}],
        )

    credential = db.get(Credential, body.credential_id)
    cloud_account = db.get(CloudAccount, credential.cloud_account_id) if credential else None
    if credential is None or cloud_account is None or cloud_account.user_id != user_id:
        # 타 사용자 소유든 존재하지 않든 동일하게 404 — 존재 여부를 노출하지 않는다.
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "credential을 찾을 수 없습니다.")
    if cloud_account.provider != provider:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "credential의 provider가 요청한 provider와 다릅니다.",
            details=[{"field": "credential_id", "reason": "provider_mismatch"}],
        )

    # sensitive_allow에 속한 필드(admin_password 등)는 DB에도, 이후 GET 응답에도 남기지
    # 않는다 — 실행기에는 아래 background_tasks.add_task로 원본 그대로 넘긴다.
    sanitized_provider_spec = {k: v for k, v in body.provider_spec.items() if k not in sensitive_allow}
    spec_json = {"common_spec": body.common_spec, "provider_spec": sanitized_provider_spec}

    existing = db.execute(
        select(ProvisioningJob).where(
            ProvisioningJob.user_id == user_id,
            ProvisioningJob.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.spec_json != spec_json
            or existing.credential_id != credential.id
            or existing.service_catalog_id != catalog_row.id
        ):
            raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "이 Idempotency-Key는 다른 요청에 이미 사용됐습니다.")
        return _job_created_response(existing)

    job = ProvisioningJob(
        user_id=user_id,
        credential_id=credential.id,
        service_catalog_id=catalog_row.id,
        workspace_name=f"pending-{uuid.uuid4().hex}",
        idempotency_key=idempotency_key,
        spec_json=spec_json,
        status="queued",
    )
    db.add(job)
    try:
        db.flush()  # job.id 확보
        job.workspace_name = f"user-{user_id}-job-{job.id}"
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(ProvisioningJob).where(
                ProvisioningJob.user_id == user_id,
                ProvisioningJob.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is None or existing.spec_json != spec_json:
            raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "이 Idempotency-Key는 다른 요청에 이미 사용됐습니다.") from None
        return _job_created_response(existing)

    db.add(
        AuditEvent(
            actor_user_id=user_id,
            action="provisioning.request",
            target_type="provisioning_job",
            target_id=str(job.id),
            result="requested",
            provider=provider,
            metadata_json={"credential_id": str(credential.id), "service_catalog_id": str(catalog_row.id)},
        )
    )
    db.commit()

    # 실행기에는 sanitize 전의 원본 common_spec/provider_spec을 그대로 넘긴다(admin_password
    # 포함) — job.spec_json(DB)에는 절대 안 남도록, 여기서 함수 인자로만 전달하고 저장하지 않는다.
    background_tasks.add_task(executor.run, job.id, body.common_spec, body.provider_spec)

    return _job_created_response(job)


@router.get("/provisioning/jobs/{job_id}")
def get_provisioning_job(
    job_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    job = db.get(ProvisioningJob, job_id)
    if job is None or job.user_id != user_id:
        raise ApiError(404, "PROVISIONING_JOB_NOT_FOUND", "프로비저닝 job을 찾을 수 없습니다.")

    error = {"code": job.error_code, "message": job.error_message} if job.error_code else None

    return {
        "data": {
            "id": str(job.id),
            "credential_id": str(job.credential_id),
            "service_catalog_id": str(job.service_catalog_id),
            "workspace_name": job.workspace_name,
            "common_spec": job.spec_json.get("common_spec", {}),
            "provider_spec": job.spec_json.get("provider_spec", {}),
            "status": job.status,
            "progress_percent": job.progress_percent,
            "created_resource_count": job.created_resource_count,
            "result": job.result_json,
            "error": error,
            "created_at": _iso(job.created_at),
            "started_at": _iso(job.started_at),
            "finished_at": _iso(job.finished_at),
        }
    }
