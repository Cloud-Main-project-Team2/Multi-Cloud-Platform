"""AWS EC2 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

이 모듈만 AWS 전용이다. spec 검증(허용 목록)과 tfvars 조립, credential → 환경변수 변환을
담당하고, 실제 subprocess 실행은 provider 무관인 `terraform_runner.run_apply()`에 위임한다.
`app/provisioning.py` 레지스트리가 이 모듈을 `(aws, ec2)`에 연결한다.

**instance_type/region 허용 목록(2026-09-11 결정)**: 실제 AWS 과금이 발생하는 리소스를 만드는
기능이라 임의 값을 그대로 Terraform에 넘기지 않는다 — `gcp_provisioning.py`와 같은 안전 원칙.
AMI는 허용 목록에 넣지 않는다: 비워두면 `backend/terraform/aws/ec2/main.tf`가 최신 Amazon Linux
2023을 자동으로 찾으므로, 사용자가 틀린 AMI ID를 넣을 여지 자체가 적다(직접 넣는 것도 허용은 함).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from app.compute_specs import ComputeCommonSpec, InboundRule
from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "aws" / "ec2"

# 라우터가 secret-필드 금지 검사에서 예외로 둘 provider_spec 필드(§10.3). AWS EC2는 CSP 계정
# 자격증명이 아닌 리소스 자체의 비밀값을 받지 않으므로 비어 있다(Azure의 admin_password 참고).
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset()

ALLOWED_INSTANCE_TYPES = ("t3.micro", "t3.small", "t3.medium")
ALLOWED_REGIONS = ("ap-northeast-2", "us-east-1")

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")
_AMI_RE = re.compile(r"^ami-[0-9a-f]{8,17}$")


def _derive(
    common_spec: dict, provider_spec: dict
) -> tuple[str, str, str, str | None, dict[str, str], list[InboundRule]]:
    """`(instance_name, region, instance_type, ami_id, tags, inbound_rules)`를 반환한다.
    실패 시 422 `ApiError`를 raise한다."""
    name = common_spec.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise validation_error(
            "common_spec.name은 소문자로 시작하는 영소문자/숫자/하이픈 2~40자여야 합니다.",
            details=[{"field": "common_spec.name", "reason": "invalid"}],
        )

    # tags/inbound_rules는 azure_provisioning.py와 같은 공유 모델(ComputeCommonSpec)로 검증한다
    # — name 자체의 형식 규칙(위)만 AWS/GCP 전용이라 별도로 남겨둔다.
    try:
        common = ComputeCommonSpec.model_validate(common_spec)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = "common_spec." + ".".join(str(p) for p in first.get("loc", ())) if first.get("loc") else "common_spec"
        raise validation_error(
            f"common_spec 검증 실패: {first.get('msg', 'invalid')}", details=[{"field": field, "reason": "invalid"}]
        ) from exc

    instance_type = provider_spec.get("instance_type")
    if instance_type not in ALLOWED_INSTANCE_TYPES:
        raise validation_error(
            f"provider_spec.instance_type은 {', '.join(ALLOWED_INSTANCE_TYPES)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.instance_type", "reason": "invalid"}],
        )

    region = provider_spec.get("region")
    if region not in ALLOWED_REGIONS:
        raise validation_error(
            f"provider_spec.region은 {', '.join(ALLOWED_REGIONS)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.region", "reason": "invalid"}],
        )

    ami_id = provider_spec.get("ami_id") or None
    if ami_id is not None and not _AMI_RE.fullmatch(ami_id):
        raise validation_error(
            "provider_spec.ami_id는 ami-로 시작하는 형식이어야 합니다.",
            details=[{"field": "provider_spec.ami_id", "reason": "invalid"}],
        )

    return f"mcp-{name}", region, instance_type, ami_id, common.tags, common.inbound_rules


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(
    job_id: int,
    instance_name: str,
    region: str,
    instance_type: str,
    ami_id: str | None,
    tags: dict[str, str] | None = None,
    inbound_rules: list[InboundRule] | None = None,
) -> dict:
    return {
        "region": region,
        "instance_type": instance_type,
        "instance_name": instance_name,
        "ami_id": ami_id,
        "tags": {**(tags or {}), "managed-by": "multi-cloud-platform", "job-id": str(job_id)},
        "inbound_rules": [rule.model_dump() for rule in (inbound_rules or [])],
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
    project_id: str | None = None,  # 라우터가 모든 러너에 동일 시그니처로 넘긴다 — AWS는 안 씀
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — 이미 `validate_spec()`을 통과한 입력이지만, raise 대신
    `TerraformResult`로 실패를 표현해 백그라운드 태스크 밖으로 예외가 새 나가지 않게 한다."""
    try:
        instance_name, region, instance_type, ami_id, tags, inbound_rules = _derive(common_spec, provider_spec)
        credential_env = _credential_env(secret_payload)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(job_id, instance_name, region, instance_type, ami_id, tags, inbound_rules)
    return run_apply(workspace_dir, MODULE_DIR, tfvars, credential_env, cancel_check=cancel_check)
