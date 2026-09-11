"""`(provider, service_code) -> 러너 모듈` 매핑.

`POST /provisioning/{provider}/{service}`가 실제로 무엇을 실행할지 여기서 조회한다.
service_catalog에는 여러 provisionable 서비스가 시드돼 있지만, 러너(Terraform 모듈 + 이 매핑의
엔트리)가 있는 조합만 실제로 생성할 수 있다 — 없는 조합은 라우터가 501
PROVISIONING_NOT_IMPLEMENTED로 응답한다.

새 provider/service를 추가하려면 `aws_provisioning.py`/`gcp_provisioning.py`/`azure_provisioning.py`
와 같은 모양의 모듈(아래 계약을 만족)을 만들고 여기 한 줄만 추가하면 된다:

- `validate_spec(common_spec, provider_spec) -> None` — 동기, 요청 처리 중 호출, 실패 시 `ApiError` raise
- `run(*, job_id, workspace_dir, project_id, common_spec, provider_spec, secret_payload, cancel_check)
   -> TerraformResult` — 비동기 백그라운드, raise하지 않고 결과로 실패 표현
- `SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str]` — secret-필드 금지 검사 예외 필드
"""

from __future__ import annotations

from types import ModuleType

from app import aws_provisioning, azure_provisioning, gcp_provisioning

_RUNNERS: dict[tuple[str, str], ModuleType] = {
    ("aws", "ec2"): aws_provisioning,
    ("azure", "vm"): azure_provisioning,
    ("gcp", "compute_engine"): gcp_provisioning,
}


def get_runner(provider: str, service_code: str) -> ModuleType | None:
    return _RUNNERS.get((provider, service_code))
