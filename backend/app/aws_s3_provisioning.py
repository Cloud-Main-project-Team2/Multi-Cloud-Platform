"""AWS S3 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

`app/aws_provisioning.py`(EC2)와 같은 flat 계약(통합 결정, 2026-09-11)을 따르는 독립 러너다.
S3는 컴퓨트가 아니므로 `ComputeCommonSpec`(inbound_rules 등)을 쓰지 않고 이 모듈 전용의
최소 common_spec만 검증한다. `app/provisioning.py` 레지스트리가 `(aws, s3)`에 연결한다.

**버킷 이름(2026-09-11 결정)**: S3 버킷 이름은 계정이 아니라 AWS 전역에서 고유해야 한다.
`mcp-{name}-{job_id}` 형태로 job_id를 접미사에 붙여 서로 다른 사용자/job이 같은 name을 써도
충돌하지 않게 한다(EC2 instance_name은 계정 범위라 job_id 접미사가 필요 없었던 것과 다른 점).

**퍼블릭 접근(2026-09-11 결정)**: provider_spec에 공개 여부 옵트인 필드를 두지 않는다 — 항상
`aws_s3_bucket_public_access_block`로 퍼블릭 접근을 전면 차단한다(EC2 inbound_rules처럼 호출자가
원하는 대로 열 수 있게 둔 시나리오가 아니라, 버킷 오정책발 데이터 유출은 되돌릴 수 없는 사고라
기본값을 강제한다). 필요해지면 별도 논의 후 명시적 필드로 추가한다.

**리소스 동기화와의 정합성**: `app/providers/aws.py`의 `discover_resources()`/
`perform_resource_action()`이 이미 S3 버킷을 `original_resource_type="S3 Bucket"`,
`region=None`(S3는 전역 서비스라 동기화 시 리전을 조회하지 않음)으로 다룬다. 이 러너가 생성한
`Resource` 행이 다음 동기화 때 같은 행으로 합쳐지도록(중복 행 방지) 이 모듈도 같은 관례를 쓴다
— `app/routers/provisioning.py`의 `_resource_attrs()` 참고.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "aws" / "s3"

# 라우터가 secret-필드 금지 검사에서 예외로 둘 provider_spec 필드(§10.3). S3는 리소스 자체의
# 비밀값을 받지 않으므로 비어 있다(Azure의 admin_password 참고).
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset()

# aws_provisioning.py(EC2)와 같은 안전 원칙 — 실제 과금이 발생하는 리소스라 임의 리전을 그대로
# Terraform에 넘기지 않는다.
ALLOWED_REGIONS = ("ap-northeast-2", "us-east-1")

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")


class _CommonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tags: dict[str, str] = Field(default_factory=dict)


class _ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    versioning_enabled: bool = False


def _derive(common_spec: dict, provider_spec: dict) -> tuple[_CommonSpec, _ProviderSpec]:
    """검증된 `(common, provider)` 모델을 반환한다. 실패 시 422 `ApiError`를 raise한다."""
    try:
        common = _CommonSpec.model_validate(common_spec)
        provider = _ProviderSpec.model_validate(provider_spec)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())) or "provider_spec"
        raise validation_error(
            f"S3 spec 검증 실패: {first.get('msg', 'invalid')}", details=[{"field": field, "reason": "invalid"}]
        ) from exc

    if not _NAME_RE.fullmatch(common.name):
        raise validation_error(
            "common_spec.name은 소문자로 시작하는 영소문자/숫자/하이픈 2~40자여야 합니다.",
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


def build_tfvars(job_id: int, common: _CommonSpec, provider: _ProviderSpec) -> dict:
    return {
        "region": provider.region,
        "bucket_name": f"mcp-{common.name}-{job_id}",
        "versioning_enabled": provider.versioning_enabled,
        "tags": {**common.tags, "managed-by": "multi-cloud-platform", "job-id": str(job_id)},
    }


def _credential_env(secret_payload: dict) -> dict[str, str]:
    access_key_id = secret_payload.get("access_key_id")
    secret_access_key = secret_payload.get("secret_access_key")
    if not access_key_id or not secret_access_key:
        raise ApiError(422, "CREDENTIAL_VERIFICATION_FAILED", "AWS 자격 증명에 access_key_id/secret_access_key가 없습니다.")

    env = {"AWS_ACCESS_KEY_ID": access_key_id, "AWS_SECRET_ACCESS_KEY": secret_access_key}
    session_token = secret_payload.get("session_token")
    if session_token:
        env["AWS_SESSION_TOKEN"] = session_token
    return env


def run(
    *,
    job_id: int,
    workspace_dir: Path,
    common_spec: dict,
    provider_spec: dict,
    secret_payload: dict,
    project_id: str | None = None,  # 라우터가 모든 러너에 동일 시그니처로 넘긴다 — S3는 안 씀
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — 이미 `validate_spec()`을 통과한 입력이지만, raise 대신
    `TerraformResult`로 실패를 표현해 백그라운드 태스크 밖으로 예외가 새 나가지 않게 한다."""
    try:
        common, provider = _derive(common_spec, provider_spec)
        credential_env = _credential_env(secret_payload)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(job_id, common, provider)
    return run_apply(workspace_dir, MODULE_DIR, tfvars, credential_env, cancel_check=cancel_check)
