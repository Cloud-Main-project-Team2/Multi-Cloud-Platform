"""Azure 무료 구독을 고려한 Compute 사양 매핑 회귀 테스트(2026-09-18).

`backend/tests/test_azure_vm_executor.py`(다른 사용자의 진행 중인 작업 — 건드리지 않는다)와
겹치지 않는 새 파일로 분리했다. 여기서는:

1. B2ats_v2(무료 대상 x86-64 대안) 선택 시 "Standard_B2ats_v2"로 정규화되는지
   (`app.azure_provisioning.ProviderSpec`, 기존 B1s/B2s 정규화와 같은 검증기 재사용).
2. B2pts_v2(ARM64)는 이미지 호환이 구현되지 않았으므로 이 검증기가 존재를 안다고 해서
   실제 이미지(Ubuntu/Windows, 둘 다 x86-64)와 함께 자동으로 써도 되는 값이 아니라는 점 —
   `IMAGE_REFERENCES`에 ARM64 이미지가 없다는 사실 자체를 고정해 회귀를 막는다.
3. `app.providers.azure.list_vm_sku_availability()`의 available/restricted/조회 실패 구분과
   ownership 검사(다른 사용자 credential로 조회 불가)는 `test_vm_sku_availability_api.py`에서
   API 레이어로 검증한다.
"""

from __future__ import annotations

from app import azure_provisioning as azure


def test_free_alt_sku_b2ats_v2_is_normalized_with_standard_prefix():
    """"경량" 등급의 무료 대상 x86-64 대안(B2ats_v2)도 B1s/B2s와 같은 정규화 규칙을 탄다 —
    프론트가 "Standard_" 접두사 없이 "B2ats_v2"만 보내도 백엔드가 "Standard_B2ats_v2"로 보정
    해야 실제로 전송되는 SKU가 UI 검토 화면과 일치한다(사용자 요청 회귀 시나리오 2번)."""
    spec = azure.ProviderSpec.model_validate(
        {
            "region": "koreacentral",
            "instance_type": "B2ats_v2",
            "admin_username": "azureuser",
            "admin_password": "S3curePassw0rd!",
        }
    )
    assert spec.instance_type == "Standard_B2ats_v2"

    already_prefixed = azure.ProviderSpec.model_validate(
        {
            "region": "koreacentral",
            "instance_type": "Standard_B2ats_v2",
            "admin_username": "azureuser",
            "admin_password": "S3curePassw0rd!",
        }
    )
    assert already_prefixed.instance_type == "Standard_B2ats_v2"


def test_arm64_free_alt_b2pts_v2_has_no_compatible_image_yet():
    """B2pts_v2(ARM64)는 Azure 무료 체험 계정의 무료 대상 SKU 안내에 포함되지만, 지금 이미지
    목록(Ubuntu 22.04/Windows Server 2022, 둘 다 x86-64 gen2 이미지)엔 ARM64 대응 항목이 없다
    — 그래서 프론트(SPEC_TIERS/AZURE_LIGHT_FREE_ALT)에서도 선택지로 노출하지 않는다(사용자
    요청 회귀 시나리오 3번). 이 테스트는 "나중에 ARM64 이미지를 추가하면서 이 사실을 깜빡 잊고
    B2pts_v2를 자동 선택 경로에 연결하는" 회귀를 막기 위한 안전장치다 — IMAGE_REFERENCES에
    ARM64 이미지가 여전히 없다는 사실 자체를 고정한다."""
    for image_ref in azure.IMAGE_REFERENCES.values():
        # 현재 등록된 이미지들의 offer/sku 어디에도 arm64 표기가 없어야 한다(전부 x86-64 gen2).
        assert "arm" not in image_ref["offer"].lower()
        assert "arm" not in image_ref["sku"].lower()
