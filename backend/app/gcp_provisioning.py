"""GCP Compute Engine VM 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

이 모듈만 GCP 전용이다. spec 검증(허용 목록)과 tfvars 조립을 담당하고, 실제 subprocess 실행은
provider 무관인 `terraform_runner.run_apply()`에 위임한다. `app/provisioning.py` 레지스트리가 이
모듈을 `(gcp, compute_engine)`에 연결한다 — 다른 러너를 추가할 때도 `validate_spec()`(동기, 요청
처리 중 raise)와 `run()`(비동기 백그라운드, raise하지 않고 `TerraformResult`로 실패를 표현) 두
함수만 맞추면 된다.

**machine_type/region 허용 목록(2026-09-11 결정, 사용자 확인)**: 실제 GCP 과금이 발생하는 리소스를
만드는 기능이라 임의 값을 그대로 Terraform에 넘기지 않는다. `frontend/assets/js/provisioning.js`의
`SPEC_TIERS`(경량/표준/고성능 → e2-micro/e2-medium/e2-standard-4)·`COUNTRY_REGION`(한국/미국 →
asia-northeast3/us-central1)과 동일한 값만 허용한다 — 프론트가 아직 이 API를 호출하지 않지만(Network
요청 0건, CLAUDE.md 기록) 같은 어휘를 써서 이후 연동 비용을 줄인다. zone은 각 region의 `-a`로 고정
(GCP_VM_생성_가이드.md 권장값과 동일).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "gcp" / "compute_vm"

ALLOWED_MACHINE_TYPES = ("e2-micro", "e2-medium", "e2-standard-4")
ALLOWED_REGIONS = ("asia-northeast3", "us-central1")
_ZONE_SUFFIX = "-a"

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")


def _derive(common_spec: dict, provider_spec: dict) -> tuple[str, str, str]:
    """`(instance_name, region, machine_type)`를 반환한다. 실패 시 422 `ApiError`를 raise한다."""
    name = common_spec.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise validation_error(
            "common_spec.name은 소문자로 시작하는 영소문자/숫자/하이픈 2~40자여야 합니다.",
            details=[{"field": "common_spec.name", "reason": "invalid"}],
        )

    machine_type = provider_spec.get("instance_type")
    if machine_type not in ALLOWED_MACHINE_TYPES:
        raise validation_error(
            f"provider_spec.instance_type은 {', '.join(ALLOWED_MACHINE_TYPES)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.instance_type", "reason": "invalid"}],
        )

    region = provider_spec.get("region")
    if region not in ALLOWED_REGIONS:
        raise validation_error(
            f"provider_spec.region은 {', '.join(ALLOWED_REGIONS)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.region", "reason": "invalid"}],
        )

    return f"mcp-{name}", region, machine_type


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, project_id: str, instance_name: str, region: str, machine_type: str) -> dict:
    return {
        "project_id": project_id,
        "region": region,
        "zone": f"{region}{_ZONE_SUFFIX}",
        "machine_type": machine_type,
        "instance_name": instance_name,
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
        instance_name, region, machine_type = _derive(common_spec, provider_spec)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    tfvars = build_tfvars(job_id, project_id, instance_name, region, machine_type)
    return run_apply(workspace_dir, MODULE_DIR, tfvars, secret_payload, cancel_check=cancel_check)
