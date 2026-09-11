"""Azure Virtual Machine 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

`app/aws_provisioning.py`·`app/gcp_provisioning.py`와 **같은 flat 계약**을 따른다(통합 결정,
2026-09-11): `validate_spec()`(동기, 요청 처리 중 raise) + `run(...)→TerraformResult`(비동기
백그라운드, raise하지 않음) 두 함수만 제공하고, job 상태 전이·감사·알림·리소스행 생성은 전부
라우터(`app/routers/provisioning.py`)가 중앙에서 처리한다. `app/provisioning.py` 레지스트리가
이 모듈을 `(azure, vm)`에 연결한다.

## admin_password 정책 (2026-09-10 결정, 통합 후에도 유지)

`frontend/assets/js/provisioning.js`가 수집하는 Azure VM 인증은 SSH 키가 아니라
사용자명/비밀번호다. `admin_password`는 §10.3이 금지하는 CSP 계정 자격증명이 아니라
"만들어질 VM 자체의 OS 접속 정보"라 secret-필드 금지 검사에서 예외로 허용한다
(`SENSITIVE_PROVIDER_SPEC_FIELDS`). 대신:

1. 라우터가 `spec_json`(DB 저장분)에서 이 필드를 제거한다 → `GET /provisioning/jobs/{id}`
   응답에 절대 나타나지 않는다.
2. 라우터는 원본 provider_spec(admin_password 포함)을 **메모리로만** 백그라운드에 넘기고,
   이 모듈이 Terraform에는 `TF_VAR_admin_password` 환경변수로만 전달한다(tfvars 파일·DB에
   평문으로 남기지 않는다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.compute_specs import ComputeCommonSpec
from app.errors import validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "azure" / "vm"

# credentials.encrypted_payload에 이 4개 키가 모두 있어야 azurerm provider를 인증할 수 있다.
REQUIRED_SECRET_FIELDS = ("tenant_id", "client_id", "client_secret", "subscription_id")

# 라우터가 secret-필드 금지 검사에서 예외로 둘 provider_spec 필드(위 "admin_password 정책" 참고).
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset({"admin_password"})

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
    """provider_spec 검증 스키마. 필드명은 `provisioning.js`의 `providerSpec.azure`(camelCase)를
    snake_case로 옮긴 것이다. 미확정 필드는 추가하지 않고 extra="forbid"로 오탈자를 막는다."""

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
        # Azure 요구사항(12~123자, 복잡도)은 최종적으로 Azure API가 검증한다 — 여기선 최소 길이만.
        if len(v) < 12:
            raise ValueError("admin_password는 Azure 요구사항상 최소 12자 이상이어야 합니다.")
        return v


def _derive(common_spec: dict, provider_spec: dict) -> tuple[ComputeCommonSpec, ProviderSpec]:
    """`(common, provider)` 검증 모델을 반환한다. 실패 시 422 `ApiError`를 raise한다."""
    try:
        common = ComputeCommonSpec.model_validate(common_spec)
        provider = ProviderSpec.model_validate(provider_spec)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())) or "provider_spec"
        raise validation_error(
            f"Azure VM spec 검증 실패: {first.get('msg', 'invalid')}",
            details=[{"field": field, "reason": "invalid"}],
        )
    return common, provider


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, workspace_name: str, common: ComputeCommonSpec, provider: ProviderSpec) -> dict:
    image_ref = IMAGE_REFERENCES[provider.image]
    tags = {**common.tags, "managed-by": "multi-cloud-platform", "job-id": str(job_id)}
    return {
        # Azure 리소스 그룹 이름은 계정 전체 범위에서 고유해야 하므로 workspace_name(=job 단위)에서
        # 파생시켜 job끼리 절대 충돌하지 않게 한다.
        "resource_group_name": f"rg-{workspace_name}"[:80],
        "location": provider.region,
        "vm_name": common.name,
        "vm_size": provider.instance_type,
        "admin_username": provider.admin_username,
        "image_publisher": image_ref["publisher"],
        "image_offer": image_ref["offer"],
        "image_sku": image_ref["sku"],
        "image_version": image_ref["version"],
        "create_public_ip": provider.create_public_ip,
        "inbound_rules": [rule.model_dump() for rule in common.inbound_rules],
        "tags": tags,
    }


def run(
    *,
    job_id: int,
    workspace_dir: Path,
    common_spec: dict,
    provider_spec: dict,
    secret_payload: dict,
    workspace_name: str | None = None,
    project_id: str | None = None,  # 라우터가 모든 러너에 동일 시그니처로 넘긴다 — Azure는 안 씀
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — raise 대신 `TerraformResult`로 실패를 표현한다.

    `provider_spec`은 라우터가 메모리로 넘긴 **원본**(admin_password 포함)이다 — DB에 저장된
    sanitize 버전이 아니다.
    """
    missing = [f for f in REQUIRED_SECRET_FIELDS if not secret_payload.get(f)]
    if missing:
        return TerraformResult(
            success=False,
            error_code="PROVIDER_AUTHENTICATION_FAILED",
            error_message=f"credential에 Azure 인증에 필요한 필드가 없습니다: {', '.join(missing)}",
        )

    try:
        common, provider = _derive(common_spec, provider_spec)
    except Exception as exc:  # validate_spec을 이미 통과했지만 방어적으로 처리
        return TerraformResult(success=False, error_code="TERRAFORM_ERROR", error_message=str(exc))

    ws_name = workspace_name or workspace_dir.name
    tfvars = build_tfvars(job_id, ws_name, common, provider)

    # ARM_* 자격 증명 + admin_password를 환경변수로만 넘긴다(tfvars 파일에 admin_password를 쓰지 않음).
    credential_env = {
        "ARM_TENANT_ID": secret_payload["tenant_id"],
        "ARM_CLIENT_ID": secret_payload["client_id"],
        "ARM_CLIENT_SECRET": secret_payload["client_secret"],
        "ARM_SUBSCRIPTION_ID": secret_payload["subscription_id"],
        "TF_VAR_admin_password": provider.admin_password,
    }
    secrets = [
        secret_payload["client_secret"],
        secret_payload["tenant_id"],
        secret_payload["client_id"],
        secret_payload["subscription_id"],
        provider.admin_password,
    ]

    return run_apply(
        workspace_dir,
        MODULE_DIR,
        tfvars,
        credential_env,
        secrets=secrets,
        cancel_check=cancel_check,
    )
