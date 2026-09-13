"""AWS CloudFront 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

`app/aws_provisioning.py`(EC2)·`app/aws_s3_provisioning.py`와 같은 flat 계약(통합 결정,
2026-09-11)을 따르는 독립 러너다. `app/provisioning.py` 레지스트리가 `(aws, cloudfront)`에
연결한다.

**오리진 범위(2026-09-11 결정)**: 커스텀 HTTPS 오리진 하나만 지원한다 — S3 전용 Origin Access
Control(OAC) + 버킷 정책 자동 구성은 이번 범위 밖이다(S3 오리진을 쓰려면 버킷이 퍼블릭 읽기를
허용하거나 사용자가 별도로 구성해야 함). `provider_spec.origin_domain_name`에 임의의 오리진
도메인(예: S3 버킷 리전 도메인, 그 외 HTTPS 오리진)을 받는다.

**리전 없음**: CloudFront는 전역 서비스라 provider_spec에 region을 받지 않는다 — Terraform
모듈이 provider 리전을 us-east-1로 고정한다(`backend/terraform/aws/cloudfront/main.tf` 참고).

**초기 상태**: `aws_cloudfront_distribution`은 기본값(`wait_for_deployment = true`)이라
Terraform apply가 배포 완료(Deployed)까지 기다린 뒤 반환한다 — 성공 시 라우터가 만드는
리소스 행의 초기 상태를 "DEPLOYED"로 둘 수 있는 이유(`app/routers/provisioning.py`의
`_initial_resource_status()` 참고). start/stop/delete 같은 리소스 제어는 이번 세션 범위 밖으로
`app/resource_actions.py`가 이미 명시적으로 막고 있다(CLAUDE.md §"지원 범위 결정" 기록).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "aws" / "cloudfront"

# 라우터가 secret-필드 금지 검사에서 예외로 둘 provider_spec 필드(§10.3). CloudFront는 리소스
# 자체의 비밀값을 받지 않으므로 비어 있다(Azure의 admin_password 참고).
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset()

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")
# 스킴/경로 없는 호스트명만 허용(예: example.s3.ap-northeast-2.amazonaws.com) — 라벨당
# 1~63자, 영숫자/하이픈, 라벨 시작/끝은 영숫자.
_DOMAIN_RE = re.compile(
    r"^(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.(?!-)[a-zA-Z0-9-]{1,63}(?<!-))+$"
)


class _CommonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tags: dict[str, str] = Field(default_factory=dict)


class _ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_domain_name: str

    @field_validator("origin_domain_name")
    @classmethod
    def _check_domain_format(cls, v: str) -> str:
        if not _DOMAIN_RE.fullmatch(v):
            raise ValueError("origin_domain_name은 스킴/경로 없는 호스트명이어야 합니다(예: example.com).")
        return v


def _derive(common_spec: dict, provider_spec: dict) -> tuple[_CommonSpec, _ProviderSpec]:
    """검증된 `(common, provider)` 모델을 반환한다. 실패 시 422 `ApiError`를 raise한다."""
    try:
        common = _CommonSpec.model_validate(common_spec)
        provider = _ProviderSpec.model_validate(provider_spec)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())) or "provider_spec"
        raise validation_error(
            f"CloudFront spec 검증 실패: {first.get('msg', 'invalid')}",
            details=[{"field": field, "reason": "invalid"}],
        ) from exc

    if not _NAME_RE.fullmatch(common.name):
        raise validation_error(
            "common_spec.name은 소문자로 시작하는 영소문자/숫자/하이픈 2~40자여야 합니다.",
            details=[{"field": "common_spec.name", "reason": "invalid"}],
        )

    return common, provider


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, common: _CommonSpec, provider: _ProviderSpec) -> dict:
    return {
        "distribution_name": common.name,
        "origin_domain_name": provider.origin_domain_name,
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
    project_id: str | None = None,  # 라우터가 모든 러너에 동일 시그니처로 넘긴다 — CloudFront는 안 씀
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — 이미 `validate_spec()`을 통과한 입력이지만, raise 대신
    `TerraformResult`로 실패를 표현해 백그라운드 태스크 밖으로 예외가 새 나가지 않게 한다.

    CloudFront 배포는 생성이 오래 걸린다(수 분) — `run_apply()`가 쓰는
    `settings.terraform_apply_timeout_seconds`가 이 시간을 감당할 만큼 충분히 커야 한다.
    """
    try:
        common, provider = _derive(common_spec, provider_spec)
        credential_env = _credential_env(secret_payload)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(job_id, common, provider)
    return run_apply(workspace_dir, MODULE_DIR, tfvars, credential_env, cancel_check=cancel_check)
