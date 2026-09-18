"""GCP Cloud Storage(Object Storage) 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

`app/gcp_provisioning.py`(Compute Engine)·`app/gcp_cloudsql_provisioning.py`(Cloud SQL)와 같은
flat 계약을 따른다: `validate_spec()`(동기, 요청 처리 중 raise) + `run(...)→TerraformResult`
(비동기 백그라운드, raise하지 않음) 두 함수와 `SENSITIVE_PROVIDER_SPEC_FIELDS` 상수만 제공한다.
job 상태 전이·감사·알림·리소스행 생성은 전부 라우터(`app/routers/provisioning.py`)가 중앙에서
처리한다. `app/provisioning.py` 레지스트리가 이 모듈을 `(gcp, cloud_storage)`에 연결한다.

## 버킷 이름에 mcp- 접두사 + job_id 접미사를 붙인다(2026-09-18 정정)

Compute 인스턴스/Cloud SQL 인스턴스는 프로젝트 범위에서만 유일하면 되지만, GCS 버킷 이름은
**GCS 전역(다른 프로젝트·다른 사용자 포함)에서 유일**해야 한다. 처음엔 이 차이 때문에
`common_spec.name`을 그대로 버킷 이름으로 써서 사용자가 전역적으로 고유한 이름을 직접 고르게
했는데(프론트 안내도 "mcp- 프리픽스 없음"이었다), 실사용 중 문제가 됐다 — 3사를 동시에 선택하면
이름 입력 하나를 공유하는데, AWS(`aws_s3_provisioning.py`)와 Azure(`azure_storage_provisioning.py`)는
이미 `mcp-{name}-{job_id}`/`mcp{name}{job_id}`로 자동으로 유일하게 만들어 주는 반면 GCP만
사용자가 타이핑한 이름을 그대로 썼다. 그 결과 "testbucket123" 같은 흔한 이름은 GCS에서 이미
다른 프로젝트가 선점했을 확률이 매우 높아 AWS/Azure는 성공하는데 GCP만 계속 실패하는
비대칭이 생겼다. **AWS와 같은 패턴으로 통일**해 `build_tfvars()`가 `mcp-{name}-{job_id}`를
최종 버킷 이름으로 쓴다 — 사용자는 더 이상 전역 유일성을 직접 보장할 필요가 없다.

## secret 없음

만들어지는 리소스 자체의 비밀값(Cloud SQL의 master_password 같은)이 없어 `SENSITIVE_PROVIDER_
SPEC_FIELDS`는 비어 있다(GCP Compute Engine과 동일).

## region/storage_class 허용 목록(2026-09-11 결정)

`region`은 다른 GCP 러너와 동일한 allow-list(asia-northeast3/us-central1)를 쓴다. `storage_class`
는 `frontend/assets/js/provisioning.js`의 `STORAGE_CLASSES`(Standard/Nearline/Coldline/Archive,
GCP 전용 ⑤ 추가 설정)와 같은 어휘를 쓰고, 여기서 GCS API가 요구하는 대문자 값(STANDARD 등)으로
변환한다. 접근제어·중복성·버전관리는 맵핑 문서 "제외" 필드라 입력을 받지 않고 Terraform 모듈이
안전한 기본값(uniform bucket-level access, public access prevention enforced, versioning
비활성)으로 고정한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "gcp" / "cloud_storage"

SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset()

ALLOWED_REGIONS = ("asia-northeast3", "us-central1")

_STORAGE_CLASS_API: dict[str, str] = {
    "Standard": "STANDARD",
    "Nearline": "NEARLINE",
    "Coldline": "COLDLINE",
    "Archive": "ARCHIVE",
}

# GCS 버킷 이름 규칙 단순화 버전(실제 GCS 규칙은 더 세밀하다 — 나머지는 API가 최종 검증한다,
# AWS/Azure 러너와 같은 원칙). 3~40자(GCS 자체 한도는 63자지만 "mcp-"(4자)+"-{job_id}"(최대
# 10자 여유) 접두/접미사를 붙일 여지를 남겨 둔다 — AWS S3의 max=40과 같은 값으로 통일했다),
# 소문자/숫자로 시작·끝, 중간엔 소문자/숫자/하이픈/언더스코어/점.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,38}[a-z0-9]$")


def _derive(common_spec: dict, provider_spec: dict) -> tuple[str, str, str]:
    """`(name, region, storage_class_api)`를 반환한다. `name`은 사용자가 입력한 값 그대로다 —
    최종 버킷 이름(`mcp-{name}-{job_id}`)은 `build_tfvars()`가 조립한다. 실패 시 422 `ApiError`를
    raise한다."""
    name = common_spec.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise validation_error(
            "common_spec.name은 소문자/숫자로 시작·끝나는 3~40자(소문자/숫자/하이픈/언더스코어/점)여야 합니다.",
            details=[{"field": "common_spec.name", "reason": "invalid"}],
        )
    if name.startswith("goog") or "google" in name:
        raise validation_error(
            "common_spec.name은 'goog'로 시작하거나 'google'을 포함할 수 없습니다(GCS 예약어).",
            details=[{"field": "common_spec.name", "reason": "reserved"}],
        )

    region = provider_spec.get("region")
    if region not in ALLOWED_REGIONS:
        raise validation_error(
            f"provider_spec.region은 {', '.join(ALLOWED_REGIONS)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.region", "reason": "invalid"}],
        )

    storage_class = provider_spec.get("storage_class")
    if storage_class not in _STORAGE_CLASS_API:
        raise validation_error(
            f"provider_spec.storage_class는 {', '.join(_STORAGE_CLASS_API)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.storage_class", "reason": "invalid"}],
        )

    return name, region, _STORAGE_CLASS_API[storage_class]


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, project_id: str, name: str, region: str, storage_class: str) -> dict:
    return {
        "project_id": project_id,
        "region": region,
        # AWS S3(`mcp-{name}-{job_id}`)와 같은 패턴 — GCS 버킷 이름은 전역에서 유일해야 해서
        # 사용자가 타이핑한 그대로 쓰면 흔한 이름은 이미 다른 프로젝트가 선점했을 확률이 높다.
        "bucket_name": f"mcp-{name}-{job_id}",
        "storage_class": storage_class,
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
    `TerraformResult`로 실패를 표현해 백그라운드 태스크 밖으로 예외가 새 나가지 않게 한다."""
    try:
        name, region, storage_class = _derive(common_spec, provider_spec)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(job_id, project_id, name, region, storage_class)
    # GCP는 secret_payload(서비스 계정 키 JSON)를 환경변수가 아니라 파일로 넘긴다(compute_engine/
    # cloud_sql과 동일) — terraform_runner가 0600 임시 파일로 써서 GOOGLE_APPLICATION_CREDENTIALS로만
    # 노출한다.
    return run_apply(
        workspace_dir, MODULE_DIR, tfvars, {}, credentials_file=secret_payload, cancel_check=cancel_check
    )
