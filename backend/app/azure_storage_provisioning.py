"""Azure Storage Account(Blob Storage, "Object Storage") 프로비저닝 —
`app/terraform_runner.py` 호출부 (§10.3).

`app/azure_provisioning.py`(VM)·`app/aws_s3_provisioning.py`·`app/gcp_storage_provisioning.py`와
같은 flat 계약(통합 결정, 2026-09-11)을 따르는 독립 러너다. Storage Account는 컴퓨트가 아니므로
`ComputeCommonSpec`을 쓰지 않고 이 모듈 전용의 최소 common_spec만 검증한다. `app/provisioning.py`
레지스트리가 `(azure, storage_account)`에 연결한다.

**계정 이름(2026-09-14 결정)**: Azure Storage Account 이름은 S3/GCS 버킷과 달리 **하이픈을 허용하지
않는다**(소문자/숫자만, 3~24자, Azure 전역 유일). `frontend/assets/js/provisioning.js`의 공용
"버킷/계정명" 입력 필드는 3사 공통이라 placeholder가 `my-unique-bucket`처럼 하이픈을 포함하지만,
이 러너는 Azure 규칙에 맞춰 **하이픈 없는 소문자/숫자만** 허용하도록 별도로 검증한다(S3/GCS도 이미
서로 다른 정규식을 쓰고 있어 같은 원칙의 연장이다). 사용자가 하이픈이 든 이름을 입력하면 422로
명확히 안내한다. 전역 유일성을 위해 S3의 `mcp-{name}-{job_id}` 패턴과 같은 목적으로
`mcp{name}{job_id}`(하이픈 없이 이어붙임)를 쓰고 24자를 넘으면 자른다.

**퍼블릭 접근/중복성/버전관리(맵핑 문서 3절 "완전 제외")**: provider_spec에 옵트인 필드를 두지
않는다 — 항상 퍼블릭 액세스를 전면 차단하고(`allow_nested_items_to_be_public=false`,
`terraform/azure/storage_account/main.tf`), 중복성은 LRS 고정, 버전관리는 비활성 고정이다(S3의
퍼블릭 접근 강제 차단과 같은 "안전한 기본값 강제" 원칙).

**리소스 동기화와의 정합성**: `app/providers/azure.py`에는 아직 Storage Account discover/action
어댑터가 없다(CLAUDE.md "resources/action SDK 어댑터 구현 범위" — Azure SQL Database/Storage
Account/CDN은 어댑터가 없어서가 아니라 의도적으로 범위 밖에 뒀다는 기존 결정과 동일선상, GCP Cloud
SQL/Storage도 마찬가지). 이 러너는 provisioning 직후 `resources` 행만 만들고, discover/action
확장은 별도 세션 범위로 남겨둔다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "azure" / "storage_account"

# Storage Account 자체에는 만들어질 리소스의 비밀값이 없다(GCP Cloud Storage와 동일 원칙).
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset()

# frontend REGIONS.azure(provisioning.js)와 동일한 allow-list — 실제 과금이 발생하는 리소스라
# 임의 리전을 그대로 Terraform에 넘기지 않는다(azure/vm은 이 검증이 없지만, S3/RDS/Cloud SQL/
# Cloud Storage 모두 이 원칙을 따르고 있어 새 러너도 동일하게 맞춘다).
ALLOWED_REGIONS = ("koreacentral", "eastus", "koreasouth", "canadacentral")

# Azure Storage Account 이름 규칙(단순화 버전, GCS 러너와 같은 원칙 — 나머지 세부 규칙은 API가
# 최종 검증한다): 소문자/숫자만, 하이픈 없이 2~21자(계정 이름 조립 시 "mcp" + 이 이름 + job_id가
# 24자를 넘지 않도록 여유를 둔다).
_NAME_RE = re.compile(r"^[a-z0-9]{2,21}$")

_ACCOUNT_NAME_MAX_LEN = 24


class _CommonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tags: dict[str, str] = Field(default_factory=dict)


class _ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str


def _derive(common_spec: dict, provider_spec: dict) -> tuple[_CommonSpec, _ProviderSpec]:
    """검증된 `(common, provider)` 모델을 반환한다. 실패 시 422 `ApiError`를 raise한다."""
    try:
        common = _CommonSpec.model_validate(common_spec)
        provider = _ProviderSpec.model_validate(provider_spec)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())) or "provider_spec"
        raise validation_error(
            f"Azure Storage Account spec 검증 실패: {first.get('msg', 'invalid')}",
            details=[{"field": field, "reason": "invalid"}],
        ) from exc

    if not _NAME_RE.fullmatch(common.name):
        raise validation_error(
            "common_spec.name은 하이픈 없이 소문자/숫자로만 이루어진 2~21자여야 합니다"
            "(Azure Storage Account 이름은 하이픈을 허용하지 않습니다).",
            details=[{"field": "common_spec.name", "reason": "invalid"}],
        )

    if provider.region not in ALLOWED_REGIONS:
        raise validation_error(
            f"provider_spec.region은 {', '.join(ALLOWED_REGIONS)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.region", "reason": "invalid"}],
        )

    return common, provider


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, workspace_name: str, common: _CommonSpec, provider: _ProviderSpec) -> dict:
    account_name = f"mcp{common.name}{job_id}"[:_ACCOUNT_NAME_MAX_LEN]
    return {
        "resource_group_name": f"rg-{workspace_name}"[:80],
        "location": provider.region,
        "account_name": account_name,
        "tags": {**common.tags, "managed-by": "multi-cloud-platform", "job-id": str(job_id)},
    }


def _credential_env(secret_payload: dict, project_id: str | None) -> dict[str, str]:
    required = ("tenant_id", "client_id", "client_secret")
    missing = [f for f in required if not secret_payload.get(f)]
    if not project_id:
        missing.append("subscription_id(cloud_account.external_account_id)")
    if missing:
        raise ApiError(
            422, "CREDENTIAL_VERIFICATION_FAILED", f"credential에 Azure 인증에 필요한 필드가 없습니다: {', '.join(missing)}"
        )
    return {
        "ARM_TENANT_ID": secret_payload["tenant_id"],
        "ARM_CLIENT_ID": secret_payload["client_id"],
        "ARM_CLIENT_SECRET": secret_payload["client_secret"],
        # 구독 ID는 secret_payload에 없다 — cloud_accounts.external_account_id로 저장되고
        # 라우터가 project_id로 넘긴다(2026-09-15 발견·수정: 마이페이지 폼/목업 시딩 어디도
        # secret_payload["subscription_id"]를 채운 적이 없어 이 러너는 UI 경로로 항상 실패했다 —
        # GCP가 project_id를 쓰는 것과 같은 패턴으로 통일).
        "ARM_SUBSCRIPTION_ID": project_id,
    }


def run(
    *,
    job_id: int,
    workspace_dir: Path,
    common_spec: dict,
    provider_spec: dict,
    secret_payload: dict,
    workspace_name: str | None = None,
    project_id: str | None = None,  # 라우터가 account.external_account_id를 넘긴다 — Azure 구독
    # ID로 쓴다(_credential_env() 참고, Storage Account 자체 리소스 ID로는 안 씀).
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — raise 대신 `TerraformResult`로 실패를 표현한다."""
    try:
        common, provider = _derive(common_spec, provider_spec)
        credential_env = _credential_env(secret_payload, project_id)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    ws_name = workspace_name or workspace_dir.name
    tfvars = build_tfvars(job_id, ws_name, common, provider)
    secrets = [secret_payload["client_secret"], secret_payload["tenant_id"], secret_payload["client_id"]]

    return run_apply(
        workspace_dir, MODULE_DIR, tfvars, credential_env, secrets=secrets, cancel_check=cancel_check
    )
