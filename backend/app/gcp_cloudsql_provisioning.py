"""GCP Cloud SQL 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

`app/aws_provisioning.py`·`app/azure_provisioning.py`·`app/gcp_provisioning.py`와 같은 flat
계약을 따른다: `validate_spec()`(동기, 요청 처리 중 raise) + `run(...)→TerraformResult`(비동기
백그라운드, raise하지 않음) 두 함수와 `SENSITIVE_PROVIDER_SPEC_FIELDS` 상수만 제공한다. job
상태 전이·감사·알림·리소스행 생성은 전부 라우터(`app/routers/provisioning.py`)가 중앙에서
처리한다. `app/provisioning.py` 레지스트리가 이 모듈을 `(gcp, cloud_sql)`에 연결한다.

## master_password 정책(Azure admin_password와 같은 방향)

`master_password`는 §10.3이 금지하는 CSP 계정 자격증명이 아니라 "만들어질 DB 인스턴스 자체의
관리자 비밀번호"라 secret-필드 금지 검사에서 예외로 허용한다(`SENSITIVE_PROVIDER_SPEC_FIELDS`).
라우터가 `spec_json`(DB 저장분)에서 이 필드를 제거하므로 `GET /provisioning/jobs/{id}` 응답에
절대 나타나지 않고, Terraform에는 `TF_VAR_root_password` 환경변수로만 전달한다(tfvars 파일에
쓰지 않는다) — Azure VM의 `admin_password`/`TF_VAR_admin_password`와 동일한 패턴이다.

## 엔진별 허용 목록과 관리자 계정 처리(2026-09-11 결정)

실제 GCP 과금이 발생하는 리소스를 만드는 기능이라 임의 값을 그대로 Terraform에 넘기지 않는다.
`region`은 `gcp_provisioning.py`(Compute Engine)와 동일한 allow-list(asia-northeast3/us-central1)를
쓴다. `engine`은 `frontend/assets/js/provisioning.js`의 `DB_ENGINES.gcp`(MySQL/PostgreSQL/
SQL Server)와 같은 어휘를 쓴다 — 프론트가 아직 이 API를 호출하지 않지만(DB는 여전히 시뮬레이션,
`SERVICE_CODE`에 db_rdbms 매핑 없음) 같은 이름을 써서 이후 연동 비용을 줄인다.

Cloud SQL은 엔진마다 관리자 계정 설정 방식이 다르다 — MySQL/PostgreSQL은 인스턴스 생성 후
`google_sql_user`로 계정을 만들고, SQL Server는 인스턴스 자체의 `root_password`로 기본 sysadmin
계정(`sqlserver`) 비밀번호를 설정한다(Cloud SQL 고유 제약, `terraform/gcp/cloud_sql/main.tf`
참고). `_ENGINE_CONFIG`가 이 차이(`database_version`/`tier`/`admin_user`/`create_admin_user`)를
캡슐화한다. `tier`는 각 엔진이 지원하는 가장 작은 유효 sku로 고정한다(PostgreSQL/SQL Server는
공유 코어 tier를 지원하지 않아 db-custom-* 최소 사양을 쓴다) — 사용자가 직접 tier를 고르는
입력은 제공하지 않는다(`SERVER_DEFAULTS.db.instanceClass`처럼 맵핑 문서상 "제외" 필드).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "gcp" / "cloud_sql"

# 라우터의 secret-필드 금지 검사 예외 필드(위 "master_password 정책" 참고). 라우터는 이 필드를
# DB 저장분(spec_json)에서 제거하고, 원본은 메모리로만 run()에 넘긴다.
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset({"master_password"})

ALLOWED_REGIONS = ("asia-northeast3", "us-central1")

# engine -> (database_version, tier, admin_user, create_admin_user).
# tier는 각 엔진이 지원하는 가장 작은 유효 sku(공유 코어는 PostgreSQL/SQL Server에 없음).
_ENGINE_CONFIG: dict[str, dict[str, object]] = {
    "MySQL": {
        "database_version": "MYSQL_8_0",
        "tier": "db-f1-micro",
        "admin_user": "root",
        "create_admin_user": True,
    },
    "PostgreSQL": {
        "database_version": "POSTGRES_15",
        "tier": "db-custom-1-3840",
        "admin_user": "postgres",
        "create_admin_user": True,
    },
    "SQL Server": {
        # Standard/Enterprise 에디션은 Cloud SQL에서 최소 4 vCPU를 요구한다(과금 부담이 커진다) —
        # Express 에디션은 1 vCPU까지 허용해 다른 엔진과 비슷한 최소 사양을 쓸 수 있다.
        "database_version": "SQLSERVER_2019_EXPRESS",
        "tier": "db-custom-1-3840",
        "admin_user": "sqlserver",
        "create_admin_user": False,
    },
}

_MIN_PASSWORD_LENGTH = 8
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")


def _derive(common_spec: dict, provider_spec: dict) -> tuple[str, str, str, dict]:
    """`(instance_name, region, master_password, engine_config)`를 반환한다.

    실패 시 422 `ApiError`를 raise한다."""
    name = common_spec.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise validation_error(
            "common_spec.name은 소문자로 시작하는 영소문자/숫자/하이픈 2~40자여야 합니다.",
            details=[{"field": "common_spec.name", "reason": "invalid"}],
        )

    region = provider_spec.get("region")
    if region not in ALLOWED_REGIONS:
        raise validation_error(
            f"provider_spec.region은 {', '.join(ALLOWED_REGIONS)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.region", "reason": "invalid"}],
        )

    engine = provider_spec.get("engine")
    engine_config = _ENGINE_CONFIG.get(engine) if isinstance(engine, str) else None
    if engine_config is None:
        raise validation_error(
            f"provider_spec.engine은 {', '.join(_ENGINE_CONFIG)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.engine", "reason": "invalid"}],
        )

    master_password = provider_spec.get("master_password")
    if not isinstance(master_password, str) or len(master_password) < _MIN_PASSWORD_LENGTH:
        raise validation_error(
            f"provider_spec.master_password는 최소 {_MIN_PASSWORD_LENGTH}자 이상이어야 합니다.",
            details=[{"field": "provider_spec.master_password", "reason": "invalid"}],
        )

    return f"mcp-{name}", region, master_password, engine_config


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, project_id: str, instance_name: str, region: str, engine_config: dict) -> dict:
    return {
        "project_id": project_id,
        "region": region,
        "instance_name": instance_name,
        "database_version": engine_config["database_version"],
        "tier": engine_config["tier"],
        "admin_user": engine_config["admin_user"],
        "create_admin_user": engine_config["create_admin_user"],
        "labels": {"managed-by": "multi-cloud-platform", "job-id": str(job_id)},
    }


def run(
    *,
    job_id: int,
    workspace_dir: Path,
    project_id: str,
    common_spec: dict,
    provider_spec: dict,
    secret_payload: dict,
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — 이미 `validate_spec()`을 통과한 입력이지만, raise 대신
    `TerraformResult`로 실패를 표현해 백그라운드 태스크 밖으로 예외가 새 나가지 않게 한다.

    `provider_spec`은 라우터가 메모리로 넘긴 **원본**(master_password 포함)이다 — DB에 저장된
    sanitize 버전이 아니다."""
    try:
        instance_name, region, master_password, engine_config = _derive(common_spec, provider_spec)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(job_id, project_id, instance_name, region, engine_config)
    # GCP는 secret_payload(서비스 계정 키 JSON)를 환경변수가 아니라 파일로 넘긴다(compute_engine과
    # 동일) — terraform_runner가 0600 임시 파일로 써서 GOOGLE_APPLICATION_CREDENTIALS로만 노출한다.
    # master_password는 tfvars 파일에 쓰지 않고 TF_VAR_root_password 환경변수로만 전달한다.
    return run_apply(
        workspace_dir,
        MODULE_DIR,
        tfvars,
        {"TF_VAR_root_password": master_password},
        credentials_file=secret_payload,
        secrets=[master_password],
        cancel_check=cancel_check,
    )
