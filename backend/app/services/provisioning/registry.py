"""`(provider, service_code) -> 실행기 모듈` 매핑.

`POST /provisioning/{provider}/{service}`가 실제로 무엇을 실행할지 여기서 조회한다.
service_catalog에는 12개 provisionable 서비스가 시드돼 있지만, 실행기(Terraform 모듈 +
이 매핑의 엔트리)가 있는 조합만 실제로 생성할 수 있다. 새 provider/service를 추가하려면
`azure_vm.py`와 같은 모양의 모듈을 만들고 여기 한 줄만 추가하면 된다.
"""

from __future__ import annotations

from types import ModuleType

from app.services.provisioning import azure_vm

EXECUTORS: dict[tuple[str, str], ModuleType] = {
    ("azure", "vm"): azure_vm,
}
