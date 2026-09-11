"""`(provider="azure", service="vm")`에 대한 프로비저닝 실행기.

라우터(`app/routers/provisioning.py`)는 이 모듈에서 세 가지를 쓴다:

- `ProviderSpec`: 요청의 `provider_spec`을 검증하는 Pydantic 모델
- `SENSITIVE_PROVIDER_SPEC_FIELDS`: secret-필드-금지 검사에서 예외로 둘 필드 이름
  (아래 "admin_password 정책" 참고)
- `run(job_id, common_spec, provider_spec)`: `ProvisioningJob` row 하나를 실행해
  상태를 종결짓는 함수 (FastAPI `BackgroundTasks`로 호출되므로 자신만의 DB 세션을 새로 연다)

## admin_password 정책 (2026-09-10 결정)

`frontend/assets/js/provisioning.js`가 실제로 수집하는 Azure VM 인증 방식은 SSH 키가
아니라 사용자명/비밀번호다. `admin_password`는 `docs/01_API_명세서_v1.1.md` 10.3절이
금지하는 "secret"(클라우드 계정 자격증명)과는 다른 종류다 — CSP API를 호출하는 자격증명이
아니라 이번에 만들어질 VM 자체의 OS 접속 정보다. 그래서:

1. `SECRET_FIELD_NOT_ALLOWED` 검사에서는 예외로 허용한다(라우터가
   `SENSITIVE_PROVIDER_SPEC_FIELDS`를 참고해 통과시킨다).
2. 그 대신 `provisioning_jobs.spec_json`에는 **저장하지 않는다** — 라우터가 저장 직전에
   이 필드를 제거하므로 `GET /provisioning/jobs/{id}` 응답에도 절대 나타나지 않는다.
3. Terraform에는 credential의 CSP 비밀값과 동일한 방식(환경변수 `TF_VAR_admin_password`)
   으로만 전달한다 — tfvars 파일이나 DB에 평문으로 남기지 않는다.

다른 provider/service를 추가할 때는 이와 같은 모양의 모듈을 만들고
`app/services/provisioning/registry.py`에 등록하면 된다.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEvent, CloudAccount, Credential, ProvisioningJob
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json
from app.services import terraform_runner
from app.services.provisioning.common_specs import ComputeCommonSpec

logger = logging.getLogger(__name__)

# credentials.encrypted_payload에 이 4개 키가 모두 있어야 azurerm provider를 인증할 수 있다.
# POST /credentials/azure 구현 시 secret_payload 스키마도 이 필드명을 따라야 한다.
REQUIRED_SECRET_FIELDS = ("tenant_id", "client_id", "client_secret", "subscription_id")

# provider_spec 중 "CSP 계정 자격증명이 아닌, 이 리소스 자체의 비밀값"이라 secret-필드
# 금지 검사에서 예외로 두는 필드. 라우터가 spec_json에 저장하기 전에 이 필드를 제거한다.
SENSITIVE_PROVIDER_SPEC_FIELDS = frozenset({"admin_password"})

# frontend COMPUTE_IMAGES.azure(provisioning.js)의 curated 목록 → 실제 Azure 이미지 참조.
IMAGE_REFERENCES: dict[str, dict[str, str]] = {
    "Ubuntu 22.04": {
        "publisher": "Canonical",
        "offer": "0001-com-ubuntu-server-jammy",
        "sku": "22_04-lts-gen2",
        "version": "latest",
    },
    "Windows Server 2022": {
        "publisher": "MicrosoftWindowsServer",
        "offer": "WindowsServer",
        "sku": "2022-datacenter-azure-edition",
        "version": "latest",
    },
}


class ProviderSpec(BaseModel):
    """provider_spec 검증 스키마. 미확정 필드는 추가하지 않고 extra="forbid"로 오탈자를 막는다.

    필드명은 `frontend/assets/js/provisioning.js`가 실제로 만드는
    `providerSpec.azure`(camelCase)를 snake_case로 그대로 옮긴 것이다:
    `region`(=ps.region), `instance_type`(=ps.instanceType), `admin_username`,
    `admin_password`, `image`(=ps.image, curated label).
    """

    model_config = ConfigDict(extra="forbid")

    region: str
    instance_type: str = "B1s"
    admin_username: str
    admin_password: str
    image: Literal["Ubuntu 22.04", "Windows Server 2022"] = "Ubuntu 22.04"
    create_public_ip: bool = True

    @field_validator("instance_type")
    @classmethod
    def _normalize_instance_type(cls, v: str) -> str:
        """프론트는 "B1s"처럼 접두사 없이 보낸다 — Azure SKU 정식 이름은 `Standard_B1s`다."""
        v = v.strip()
        if v.lower().startswith(("standard_", "basic_")):
            return v
        return f"Standard_{v}"

    @field_validator("admin_password")
    @classmethod
    def _check_password_length(cls, v: str) -> str:
        # Azure 자체 요구사항(12~123자, 복잡도 3/4)은 여기서 다 검증하지 않고 최소 길이만
        # 걸러 명확한 422로 실패하게 한다 — 나머지는 Azure API가 최종 검증한다.
        if len(v) < 12:
            raise ValueError("admin_password는 Azure 요구사항상 최소 12자 이상이어야 합니다.")
        return v


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


def run(job_id: int, common_spec: dict, provider_spec: dict) -> None:
    """`ProvisioningJob`을 실행한다. 예외를 밖으로 던지지 않는다 — 반드시 status를 종결시킨다.

    `common_spec`/`provider_spec`은 라우터가 요청 본문에서 그대로 넘겨준 것이다(=
    `admin_password`가 포함된 원본). `job.spec_json`(DB에 저장된 sanitize된 버전)에서
    다시 읽지 않는다 — 그러면 admin_password를 DB에 저장하는 꼴이 되기 때문이다.
    """
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
            spec = ProviderSpec.model_validate(provider_spec)
            common = ComputeCommonSpec.model_validate(common_spec)
        except ValidationError as exc:
            _fail(db, job, "TERRAFORM_ERROR", f"spec 검증 실패: {exc.errors()[:5]}")
            return

        module_dir = Path(settings.terraform_modules_dir) / "azure" / "vm"
        workspace_dir = Path(settings.terraform_runs_dir) / job.workspace_name
        terraform_runner.prepare_workspace(module_dir, workspace_dir)

        image_ref = IMAGE_REFERENCES[spec.image]
        tags = {**common.tags, "managed_by": "multicloud-platform", "job_id": str(job.id)}

        tfvars = {
            # Azure 리소스 그룹 이름은 계정 전체 범위에서 고유해야 하므로 workspace_name(=job 단위)
            # 에서 파생시켜 job끼리 절대 충돌하지 않게 한다.
            "resource_group_name": f"rg-{job.workspace_name}"[:80],
            "location": spec.region,
            "vm_name": common.name,
            "vm_size": spec.instance_type,
            "admin_username": spec.admin_username,
            "image_publisher": image_ref["publisher"],
            "image_offer": image_ref["offer"],
            "image_sku": image_ref["sku"],
            "image_version": image_ref["version"],
            "create_public_ip": spec.create_public_ip,
            "inbound_rules": [rule.model_dump() for rule in common.inbound_rules],
            "tags": tags,
        }
        # admin_password는 tfvars 파일(디스크)에 쓰지 않는다 — TF_VAR_ 환경변수로만 전달한다.
        (workspace_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars), encoding="utf-8")

        plugin_cache_dir = Path(settings.terraform_plugin_cache_dir)
        plugin_cache_dir.mkdir(parents=True, exist_ok=True)

        env = {
            **os.environ,
            "ARM_TENANT_ID": secret_payload["tenant_id"],
            "ARM_CLIENT_ID": secret_payload["client_id"],
            "ARM_CLIENT_SECRET": secret_payload["client_secret"],
            "ARM_SUBSCRIPTION_ID": secret_payload["subscription_id"],
            "TF_VAR_admin_password": spec.admin_password,
            # job마다 새 workspace에서 init하므로 provider plugin을 공유 캐시로 재사용한다.
            "TF_PLUGIN_CACHE_DIR": str(plugin_cache_dir),
        }
        secrets_to_redact = [
            secret_payload["client_secret"],
            secret_payload["tenant_id"],
            secret_payload["client_id"],
            secret_payload["subscription_id"],
            spec.admin_password,
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
