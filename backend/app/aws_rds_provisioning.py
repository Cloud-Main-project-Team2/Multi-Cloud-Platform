"""AWS RDS 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

`app/aws_provisioning.py`(EC2)·`app/aws_s3_provisioning.py`와 같은 flat 계약(통합 결정,
2026-09-11)을 따르는 독립 러너다. `app/provisioning.py` 레지스트리가 `(aws, rds)`에 연결한다.

**master_username 미노출(2026-09-11 결정)**: AWS는 engine별로 "admin" 등 예약어를
master_username으로 못 쓰게 막는데(엔진마다 예약어 목록이 달라 사전 검증이 번거롭다), 사용자가
직접 고를 실익도 적어 `mcp_admin`으로 고정한다(EC2의 ami_id 자동 선택, S3의 퍼블릭 접근 강제
차단과 같은 "안전한 기본값 강제" 원칙). `master_password`만 provider_spec으로 받는다 — Azure의
`admin_password`와 같은 성격(CSP 계정 자격증명이 아니라 만들어질 리소스 자체의 로그인 정보)이라
`SENSITIVE_PROVIDER_SPEC_FIELDS`로 선언해 DB(`spec_json`)엔 저장하지 않고 Terraform에는
`TF_VAR_master_password` 환경변수로만 전달한다.

**engine_version 미노출**: EC2의 `ami_id=null` 자동 선택과 같은 이유로 사용자에게 받지 않는다 —
특정 마이너 버전을 고정하면 리전/시점에 따라 AWS가 그 버전을 폐기(deprecate)해 apply가 실패하기
쉽다. Terraform 리소스에서 `engine_version`을 아예 지정하지 않아 AWS가 그 시점의 기본 버전을
고르게 둔다(`backend/terraform/aws/rds/main.tf` 참고).

**네트워크(2026-09-11 결정)**: RDS 인스턴스는 항상 `publicly_accessible=false`이고, 보안 그룹은
기본 VPC의 CIDR 대역에서만 DB 포트 접근을 허용한다(EC2 inbound_rules처럼 호출자가 여는 게
아니라 이 리소스는 항상 비공개 — S3의 퍼블릭 접근 전면 차단과 같은 원칙). 같은 기본 VPC의 EC2
인스턴스에서는 접속할 수 있다.

**리소스 동기화와의 정합성**: `app/providers/aws.py`의 `discover_resources()`/
`perform_resource_action()`이 이미 RDS 인스턴스를 `original_resource_type="RDS Instance"`,
`external_resource_id=DBInstanceIdentifier`로 다룬다. 이 러너가 생성한 `Resource` 행이 다음
동기화 때 같은 행으로 합쳐지도록 같은 관례를 쓴다 — `app/routers/provisioning.py`의
`_resource_attrs()` 참고.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "aws" / "rds"

# 라우터가 secret-필드 금지 검사에서 예외로 둘 provider_spec 필드(§10.3) — "master_password
# 미노출" 결정 참고(Azure의 admin_password와 같은 성격).
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset({"master_password"})

# aws_provisioning.py(EC2)와 같은 안전 원칙 — 실제 과금이 발생하는 리소스라 임의 값을 그대로
# Terraform에 넘기지 않는다.
ALLOWED_REGIONS = ("ap-northeast-2", "us-east-1")
ALLOWED_ENGINES = ("mysql", "postgres")
ALLOWED_INSTANCE_CLASSES = ("db.t3.micro", "db.t3.small", "db.t3.medium")

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")
# RDS 마스터 비밀번호 허용 문자 규칙(AWS 공통: 8~41자, '/', '"', '@', 공백 금지).
_PASSWORD_FORBIDDEN_CHARS = set('/"@ ')


class _CommonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tags: dict[str, str] = Field(default_factory=dict)


class _ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    engine: str
    instance_class: str = "db.t3.micro"
    master_password: str

    @field_validator("master_password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        if not (8 <= len(v) <= 41) or any(c in _PASSWORD_FORBIDDEN_CHARS for c in v):
            raise ValueError(
                "master_password는 8~41자여야 하고 '/', '\"', '@', 공백을 포함할 수 없습니다."
            )
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
            f"RDS spec 검증 실패: {first.get('msg', 'invalid')}", details=[{"field": field, "reason": "invalid"}]
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

    if provider.engine not in ALLOWED_ENGINES:
        raise validation_error(
            f"provider_spec.engine은 {', '.join(ALLOWED_ENGINES)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.engine", "reason": "invalid"}],
        )

    if provider.instance_class not in ALLOWED_INSTANCE_CLASSES:
        raise validation_error(
            f"provider_spec.instance_class는 {', '.join(ALLOWED_INSTANCE_CLASSES)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.instance_class", "reason": "invalid"}],
        )

    return common, provider


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, common: _CommonSpec, provider: _ProviderSpec) -> dict:
    return {
        "region": provider.region,
        "instance_name": f"mcp-{common.name}",
        "engine": provider.engine,
        "instance_class": provider.instance_class,
        "db_name": common.name.replace("-", "_"),
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
    project_id: str | None = None,  # 라우터가 모든 러너에 동일 시그니처로 넘긴다 — RDS는 안 씀
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — 이미 `validate_spec()`을 통과한 입력이지만, raise 대신
    `TerraformResult`로 실패를 표현해 백그라운드 태스크 밖으로 예외가 새 나가지 않게 한다.

    `provider_spec`은 라우터가 메모리로 넘긴 **원본**(master_password 포함)이다 — DB에 저장된
    sanitize 버전이 아니다.
    """
    try:
        common, provider = _derive(common_spec, provider_spec)
        credential_env = _credential_env(secret_payload)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(job_id, common, provider)
    # tfvars 파일에는 master_password를 쓰지 않는다 — Azure의 TF_VAR_admin_password와 같은 경로.
    credential_env["TF_VAR_master_password"] = provider.master_password
    secrets = [provider.master_password]

    return run_apply(workspace_dir, MODULE_DIR, tfvars, credential_env, secrets=secrets, cancel_check=cancel_check)
