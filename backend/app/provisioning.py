"""`(provider, service_code) -> 프로비저닝 러너 모듈` 레지스트리 (§10.3).

`app/resource_actions.py`의 `supported_actions()`/`perform_action()`과 같은 패턴이다:
`service_catalog`에는 12개 provider×service 조합이 모두 `provisionable=true`로 있지만, 실제
Terraform 러너가 붙은 건 GCP Compute Engine 하나뿐이다(2026-09-11 결정, CLAUDE.md 기록) — 어댑터가
없어서가 아니라 이번 세션 범위 밖으로 의도적으로 뺀 것이다. 카탈로그에는 있지만 러너가 없는 조합은
`app/routers/provisioning.py`가 `501 PROVISIONING_NOT_IMPLEMENTED`로 명확히 응답한다(`resources.py`의
`DELETE /cloud-accounts/{id}` 501 선례와 동일한 모양).

러너 모듈은 두 함수만 맞추면 된다:
- `validate_spec(common_spec, provider_spec) -> None` — 요청 처리 중 동기 호출, 실패 시 422 `ApiError`.
- `run(*, job_id, workspace_dir, project_id, common_spec, provider_spec, secret_payload, cancel_check)
  -> TerraformResult` — 백그라운드 실행, raise하지 않고 결과로 실패를 표현.
"""

from __future__ import annotations

from types import ModuleType

from app import gcp_provisioning

_RUNNERS: dict[tuple[str, str], ModuleType] = {
    ("gcp", "compute_engine"): gcp_provisioning,
}


def get_runner(provider: str, service_code: str) -> ModuleType | None:
    return _RUNNERS.get((provider, service_code))
