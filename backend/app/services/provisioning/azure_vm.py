"""`(provider="azure", service="vm")`에 대한 프로비저닝 실행기.

라우터(`app/routers/provisioning.py`)는 이 모듈에서 두 가지만 사용한다:

- `ProviderSpec`: 요청의 `provider_spec`을 검증하는 Pydantic 모델
- `run(job_id)`: `ProvisioningJob` row 하나를 실행해 상태를 종결짓는 함수
  (FastAPI `BackgroundTasks`로 호출되므로 자신만의 DB 세션을 새로 연다)

다른 provider/service를 추가할 때는 이와 같은 모양의 모듈을 만들고
`app/services/provisioning/registry.py`에 등록하면 된다.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEvent, CloudAccount, Credential, ProvisioningJob
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json
from app.services import terraform_runner

logger = logging.getLogger(__name__)

# credentials.encrypted_payload에 이 4개 키가 모두 있어야 azurerm provider를 인증할 수 있다.
# POST /credentials/azure 구현 시 secret_payload 스키마도 이 필드명을 따라야 한다.
REQUIRED_SECRET_FIELDS = ("tenant_id", "client_id", "client_secret", "subscription_id")


class ProviderSpec(BaseModel):
    """provider_spec 검증 스키마. 미확정 필드는 추가하지 않고 extra="forbid"로 오탈자를 막는다."""

    model_config = ConfigDict(extra="forbid")

    location: str
    vm_size: str = "Standard_B1s"
    admin_username: str
    ssh_public_key: str
    image_publisher: str = "Canonical"
    image_offer: str = "0001-com-ubuntu-server-jammy"
    image_sku: str = "22_04-lts-gen2"
    image_version: str = "latest"
    create_public_ip: bool = True


def _iso_now() -> datetime:
    return datetime.now(timezone.utc)


def _fail(db: Session, job: ProvisioningJob, code: str, message: str) -> None:
    job.status = "failed"
    job.error_code = code
    job.error_message = message[:2000]
    job.finished_at = _iso_now()
    db.commit()
    db.add(
        AuditEvent(
            actor_user_id=job.user_id,
            action="provisioning.complete",
            target_type="provisioning_job",
            target_id=str(job.id),
            result="failure",
            metadata_json={"error_code": code},
        )
    )
    db.commit()


def run(job_id: int) -> None:
    """`ProvisioningJob`을 실행한다. 예외를 밖으로 던지지 않는다 — 반드시 status를 종결시킨다."""
    settings = get_settings()
    db = SessionLocal()
    try:
        job = db.get(ProvisioningJob, job_id)
        if job is None:
            logger.error("provisioning job %s not found when starting execution", job_id)
            return

        job.status = "running"
        job.started_at = _iso_now()
        db.commit()

        credential = db.get(Credential, job.credential_id)
        cloud_account = db.get(CloudAccount, credential.cloud_account_id) if credential else None
        if credential is None or cloud_account is None:
            _fail(db, job, "CREDENTIAL_NOT_FOUND", "job이 참조하는 credential을 찾을 수 없습니다.")
            return

        try:
            secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
        except CredentialEncryptionError:
            _fail(db, job, "PROVIDER_AUTHENTICATION_FAILED", "credential 복호화에 실패했습니다.")
            return

        missing = [f for f in REQUIRED_SECRET_FIELDS if not secret_payload.get(f)]
        if missing:
            _fail(
                db,
                job,
                "PROVIDER_AUTHENTICATION_FAILED",
                f"credential에 Azure 인증에 필요한 필드가 없습니다: {', '.join(missing)}",
            )
            return

        try:
            provider_spec = ProviderSpec.model_validate(job.spec_json.get("provider_spec", {}))
        except ValidationError as exc:
            _fail(db, job, "TERRAFORM_ERROR", f"provider_spec 검증 실패: {exc.errors()[:5]}")
            return

        vm_name = job.spec_json.get("common_spec", {}).get("name")
        if not vm_name:
            _fail(db, job, "TERRAFORM_ERROR", "common_spec.name이 없습니다.")
            return

        module_dir = Path(settings.terraform_modules_dir) / "azure" / "vm"
        workspace_dir = Path(settings.terraform_runs_dir) / job.workspace_name
        terraform_runner.prepare_workspace(module_dir, workspace_dir)

        tfvars = {
            # Azure 리소스 그룹 이름은 계정 전체 범위에서 고유해야 하므로 workspace_name(=job 단위)
            # 에서 파생시켜 job끼리 절대 충돌하지 않게 한다.
            "resource_group_name": f"rg-{job.workspace_name}"[:80],
            "location": provider_spec.location,
            "vm_name": vm_name,
            "vm_size": provider_spec.vm_size,
            "admin_username": provider_spec.admin_username,
            "ssh_public_key": provider_spec.ssh_public_key,
            "image_publisher": provider_spec.image_publisher,
            "image_offer": provider_spec.image_offer,
            "image_sku": provider_spec.image_sku,
            "image_version": provider_spec.image_version,
            "create_public_ip": provider_spec.create_public_ip,
            "tags": {"managed_by": "multicloud-platform", "job_id": str(job.id)},
        }
        (workspace_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars), encoding="utf-8")

        plugin_cache_dir = Path(settings.terraform_plugin_cache_dir)
        plugin_cache_dir.mkdir(parents=True, exist_ok=True)

        env = {
            **os.environ,
            "ARM_TENANT_ID": secret_payload["tenant_id"],
            "ARM_CLIENT_ID": secret_payload["client_id"],
            "ARM_CLIENT_SECRET": secret_payload["client_secret"],
            "ARM_SUBSCRIPTION_ID": secret_payload["subscription_id"],
            # job마다 새 workspace에서 init하므로 provider plugin을 공유 캐시로 재사용한다.
            "TF_PLUGIN_CACHE_DIR": str(plugin_cache_dir),
        }
        secrets_to_redact = [
            secret_payload["client_secret"],
            secret_payload["tenant_id"],
            secret_payload["client_id"],
            secret_payload["subscription_id"],
        ]

        # job.terraform_state_ref에는 state 본문이 아니라 로컬 경로 참조만 저장한다.
        # terraform/README.md "알려진 한계" 참고 — 운영 전 원격 backend로 교체 필요.
        job.terraform_state_ref = str(workspace_dir / "terraform.tfstate")
        db.commit()

        try:
            result = terraform_runner.apply(
                workspace_dir, env, secrets_to_redact, settings.terraform_timeout_seconds
            )
        except terraform_runner.TerraformError as exc:
            _fail(db, job, "TERRAFORM_ERROR", str(exc))
            return

        if not result.ok:
            _fail(db, job, "TERRAFORM_ERROR", result.stderr or result.stdout or "terraform apply failed")
            return

        try:
            outputs_raw = terraform_runner.output_json(
                workspace_dir, env, secrets_to_redact, settings.terraform_timeout_seconds
            )
            outputs = json.loads(outputs_raw)
        except (terraform_runner.TerraformError, json.JSONDecodeError) as exc:
            # VM 자체는 생성됐을 수 있으므로 실패가 아니라 output 누락으로만 기록한다.
            outputs = {}
            logger.warning("job %s: terraform output 파싱 실패: %s", job.id, exc)

        job.result_json = {
            "vm_id": outputs.get("vm_id", {}).get("value"),
            "resource_group_name": outputs.get("resource_group_name", {}).get("value"),
            "private_ip_address": outputs.get("private_ip_address", {}).get("value"),
            "public_ip_address": outputs.get("public_ip_address", {}).get("value"),
        }
        job.status = "success"
        job.progress_percent = 100
        job.created_resource_count = 1
        job.finished_at = _iso_now()
        db.commit()

        db.add(
            AuditEvent(
                actor_user_id=job.user_id,
                action="provisioning.complete",
                target_type="provisioning_job",
                target_id=str(job.id),
                result="success",
                provider="azure",
                metadata_json={"created_resource_count": 1},
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 — job을 running 상태로 남겨두지 않기 위한 최종 안전망
        logger.exception("provisioning job %s failed with an unexpected error", job_id)
        db.rollback()
        job = db.get(ProvisioningJob, job_id)
        if job is not None and job.status != "success":
            job.status = "failed"
            job.error_code = "TERRAFORM_ERROR"
            job.error_message = "예상하지 못한 오류로 프로비저닝이 중단됐습니다."
            job.finished_at = _iso_now()
            db.commit()
    finally:
        db.close()
