"""`(provider, service_code) -> 프로비저닝 러너 모듈` 레지스트리 (§10.3).

`service_catalog`에는 12개 provider×service 조합이 모두 `provisionable=true`로 있지만, 실제
Terraform 러너가 붙은 건 AWS EC2 하나뿐이다(2026-09-11, `jongkuk/aws-provisioning` 세션 범위) —
어댑터가 없어서가 아니라 이번 세션 범위 밖으로 의도적으로 뺀 것이다. 카탈로그에는 있지만 러너가
없는 조합은 `app/routers/provisioning.py`가 `501 PROVISIONING_NOT_IMPLEMENTED`로 명확히 응답한다.

러너 모듈은 두 함수만 맞추면 된다:
- `validate_spec(common_spec, provider_spec) -> None` — 요청 처리 중 동기 호출, 실패 시 422 `ApiError`.
- `run(*, job_id, workspace_dir, common_spec, provider_spec, secret_payload, cancel_check)
  -> TerraformResult` — 백그라운드 실행, raise하지 않고 결과로 실패를 표현. `secret_payload`는
  credential을 복호화한 원본 dict이며, 그걸 실제 provider 인증 방식(AWS는 env var, GCP는
  서비스 계정 JSON 파일 등)으로 바꾸는 건 각 러너 모듈의 책임이다.
"""

from __future__ import annotations

from types import ModuleType

from app import aws_provisioning

_RUNNERS: dict[tuple[str, str], ModuleType] = {
    ("aws", "ec2"): aws_provisioning,
}


def get_runner(provider: str, service_code: str) -> ModuleType | None:
    return _RUNNERS.get((provider, service_code))
