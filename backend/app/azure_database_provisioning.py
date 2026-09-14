"""Azure Database(MySQL/PostgreSQL/SQL Server, service_catalog상 `sql_database`) 프로비저닝 —
`app/terraform_runner.py` 호출부 (§10.3).

`app/azure_provisioning.py`(VM)·`app/azure_storage_provisioning.py`·`app/gcp_cloudsql_provisioning.py`
와 같은 flat 계약(통합 결정, 2026-09-11)을 따르는 독립 러너다. `app/provisioning.py` 레지스트리가
이 모듈을 `(azure, sql_database)`에 연결한다.

## 왜 러너 하나가 Terraform 모듈 3개를 씀 (2026-09-14 결정)

`service_catalog`엔 `(azure, sql_database)` 한 행만 시딩돼 있지만, 프론트(`provisioning.js`의
`DB_ENGINES.azure`)가 실제로 보내는 엔진은 MySQL/PostgreSQL/SQL Server 3종이다. AWS RDS·GCP
Cloud SQL은 "한 Terraform 리소스 타입 + engine 파라미터"로 멀티 엔진을 처리하지만, Azure는 이 셋이
서로 다른 ARM 리소스 타입(`azurerm_mysql_flexible_server`/`azurerm_postgresql_flexible_server`/
`azurerm_mssql_server`)이라 그 패턴을 그대로 못 쓴다. 새 `service_catalog` 행이나 마이그레이션을
추가하지 않기 위해, 이 러너가 `engine` 값에 따라
`backend/terraform/azure/database/{mysql,postgresql,sql_server}/` 중 하나의 서브모듈을 선택해서
실행한다(`terraform_runner.py`는 수정하지 않았다 — 모듈 경로를 호출부가 넘기는 기존 구조 그대로 활용).
자세한 배경은 `docs/Azure Storage·Database 프로비저닝 구현 결정사항 (2026-09-14).md` 참고.

## 네트워크 정책 — 비공개 전용(2026-09-14 결정, GCP Cloud SQL과 다름)

이 플랫폼은 실제 사용자 자격증명으로 실제 리소스를 만드는 도구이고, 지금까지의 다른 서비스
(S3 퍼블릭 차단, RDS 비공개, CloudFront 제한적 오리진)가 전부 "기본값은 항상 안전하게" 원칙을
따른다. GCP Cloud SQL 러너는 데모 편의를 위해 퍼블릭 IP + 방화벽 전체 개방을 택했지만, 이 러너는
그 선례를 따르지 않고 **AWS RDS와 같은 수준(비공개, 인터넷 노출 없음)**으로 만든다 — 세 Terraform
서브모듈 모두 자기 VNet(+MySQL/PostgreSQL은 위임된 서브넷, SQL Server는 Private Endpoint)을 갖춘다.

## master_username을 사용자 입력으로 받는 이유 (AWS/GCP와 다른 점)

AWS RDS는 `master_username`을 아예 안 받고 서버가 `mcp_admin`으로 고정한다(엔진마다 예약어가
달라 사전 검증이 번거롭다는 이유). 하지만 `frontend/assets/js/provisioning.js`의 `dbBoxEl()`은
**Azure만** 마스터 사용자명을 사용자 입력으로 받도록 이미 구현돼 있어(AWS/GCP는 비밀번호만), 이
러너는 그 입력을 받아 직접 예약어/형식 검증을 한다(`_USERNAME_RE`, `_RESERVED_USERNAMES`).

## master_password 정책(Azure admin_password/RDS master_password와 같은 방향, 검증은 더 엄격)

`master_password`는 만들어질 DB 서버 자체의 관리자 비밀번호라 secret-필드 금지 검사에서 예외로
허용한다(`SENSITIVE_PROVIDER_SPEC_FIELDS`). 라우터가 `spec_json`(DB 저장분)에서 제거하므로 `GET
/provisioning/jobs/{id}` 응답에 남지 않고, Terraform에는 `TF_VAR_admin_password` 환경변수로만
전달한다(세 서브모듈 모두 변수명을 `admin_password`로 통일). Azure Database 서비스들이 실제로
"8자 이상 + 대문자/소문자/숫자/특수문자 중 3종 이상"을 요구하므로, AWS(금지 문자만)/GCP(길이만)
보다 엄격한 복잡도 검증을 추가했다 — apply 단계 실패 대신 `validate_spec()`(422)에서 미리 걸러진다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_BASE = Path(__file__).resolve().parent.parent / "terraform" / "azure" / "database"

# engine(프론트 DB_ENGINES.azure와 동일 어휘) -> Terraform 서브모듈 디렉터리 이름.
_ENGINE_MODULE: dict[str, str] = {
    "MySQL": "mysql",
    "PostgreSQL": "postgresql",
    "SQL Server": "sql_server",
}

# 라우터가 secret-필드 금지 검사에서 예외로 둘 provider_spec 필드(위 "master_password 정책" 참고).
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset({"master_password"})

# frontend REGIONS.azure(provisioning.js)와 동일한 allow-list(azure_storage_provisioning.py와 동일).
ALLOWED_REGIONS = ("koreacentral", "eastus", "koreasouth", "canadacentral")

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")  # AWS RDS/GCP Cloud SQL과 동일 규칙

# 관리자 계정명 형식(단순화 버전 — 나머지 세부 규칙은 Azure API가 최종 검증한다).
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")

# MySQL/PostgreSQL Flexible Server·Azure SQL이 공통으로 막는 예약어의 보수적인 합집합 —
# 3사 각각의 정확한 예약어 목록과 100% 일치를 보장하지는 않는다(최종 검증은 Azure가 함).
_RESERVED_USERNAMES = frozenset(
    {
        "admin", "administrator", "root", "guest", "public", "sa",
        "azure_superuser", "azuresu", "sysadmin", "information_schema",
        "sys", "master", "model", "msdb", "tempdb",
    }
)

_MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_LENGTH = 128
_MAX_SERVER_NAME_LEN = 63


class _CommonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tags: dict[str, str] = Field(default_factory=dict)


class _ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    engine: Literal["MySQL", "PostgreSQL", "SQL Server"]
    master_username: str
    master_password: str

    @field_validator("master_password")
    @classmethod
    def _check_password_complexity(cls, v: str) -> str:
        if not (_MIN_PASSWORD_LENGTH <= len(v) <= _MAX_PASSWORD_LENGTH):
            raise ValueError(
                f"master_password는 {_MIN_PASSWORD_LENGTH}~{_MAX_PASSWORD_LENGTH}자여야 합니다."
            )
        classes = (
            any(c.islower() for c in v),
            any(c.isupper() for c in v),
            any(c.isdigit() for c in v),
            any(not c.isalnum() for c in v),
        )
        if sum(classes) < 3:
            raise ValueError(
                "master_password는 대문자/소문자/숫자/특수문자 중 3종 이상을 포함해야 합니다."
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
            f"Azure Database spec 검증 실패: {first.get('msg', 'invalid')}",
            details=[{"field": field, "reason": "invalid"}],
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

    if not _USERNAME_RE.fullmatch(provider.master_username):
        raise validation_error(
            "provider_spec.master_username은 영문자로 시작하는 영문/숫자/밑줄 1~63자여야 합니다.",
            details=[{"field": "provider_spec.master_username", "reason": "invalid"}],
        )

    if provider.master_username.lower() in _RESERVED_USERNAMES:
        raise validation_error(
            f"provider_spec.master_username은 예약어({provider.master_username})를 쓸 수 없습니다.",
            details=[{"field": "provider_spec.master_username", "reason": "reserved"}],
        )

    return common, provider


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(
    job_id: int, workspace_name: str, common: _CommonSpec, provider: _ProviderSpec
) -> tuple[dict, Path]:
    """`(tfvars, module_dir)`를 반환한다 — 엔진마다 다른 서브모듈을 골라야 하므로 module_dir도 같이 준다."""
    # 서버 이름은 Azure 전역에서 고유해야 한다(RDS/Cloud SQL은 계정/프로젝트 범위라 job_id 접미사가
    # 필요 없었지만, Azure DB 서버는 S3 버킷과 같은 이유로 필요하다).
    server_name = f"mcp-{common.name}-{job_id}"[:_MAX_SERVER_NAME_LEN]
    tfvars = {
        "resource_group_name": f"rg-{workspace_name}"[:80],
        "location": provider.region,
        "server_name": server_name,
        "admin_login": provider.master_username,
        "database_name": common.name.replace("-", "_"),
        "tags": {**common.tags, "managed-by": "multi-cloud-platform", "job-id": str(job_id)},
    }
    module_dir = MODULE_BASE / _ENGINE_MODULE[provider.engine]
    return tfvars, module_dir


def _credential_env(secret_payload: dict) -> dict[str, str]:
    required = ("tenant_id", "client_id", "client_secret", "subscription_id")
    missing = [f for f in required if not secret_payload.get(f)]
    if missing:
        raise ApiError(
            422, "CREDENTIAL_VERIFICATION_FAILED", f"credential에 Azure 인증에 필요한 필드가 없습니다: {', '.join(missing)}"
        )
    return {
        "ARM_TENANT_ID": secret_payload["tenant_id"],
        "ARM_CLIENT_ID": secret_payload["client_id"],
        "ARM_CLIENT_SECRET": secret_payload["client_secret"],
        "ARM_SUBSCRIPTION_ID": secret_payload["subscription_id"],
    }


def run(
    *,
    job_id: int,
    workspace_dir: Path,
    common_spec: dict,
    provider_spec: dict,
    secret_payload: dict,
    workspace_name: str | None = None,
    project_id: str | None = None,  # 라우터가 모든 러너에 동일 시그니처로 넘긴다 — Azure Database는 안 씀
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — raise 대신 `TerraformResult`로 실패를 표현한다.

    `provider_spec`은 라우터가 메모리로 넘긴 **원본**(master_password 포함)이다 — DB에 저장된
    sanitize 버전이 아니다.
    """
    try:
        common, provider = _derive(common_spec, provider_spec)
        credential_env = _credential_env(secret_payload)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    ws_name = workspace_name or workspace_dir.name
    tfvars, module_dir = build_tfvars(job_id, ws_name, common, provider)

    credential_env["TF_VAR_admin_password"] = provider.master_password
    secrets = [
        secret_payload["client_secret"],
        secret_payload["tenant_id"],
        secret_payload["client_id"],
        provider.master_password,
    ]

    return run_apply(
        workspace_dir, module_dir, tfvars, credential_env, secrets=secrets, cancel_check=cancel_check
    )
