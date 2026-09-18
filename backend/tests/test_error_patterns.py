"""원문 패턴 번역기(`app.error_patterns`) 검증."""

from __future__ import annotations

import pytest

from app.error_patterns import translate_reason


def test_uppercase_bucket_name_is_translated():
    # 사용자 예시: AWS S3 버킷 이름에 대문자를 써서 실패한 terraform 원문.
    raw = (
        'Error: expected name to contain only lowercase alphanumeric characters '
        'and hyphens, got "MyBucketNAME"'
    )
    msg = translate_reason(raw, code="TERRAFORM_ERROR")
    assert msg is not None
    assert "소문자" in msg


def test_invalid_bucket_name_code_variant():
    raw = "InvalidBucketName: The specified bucket is not valid."
    assert "소문자" in (translate_reason(raw) or "")


@pytest.mark.parametrize(
    "raw, expect",
    [
        ("BucketAlreadyExists: the requested bucket name is not available", "이미"),
        ("Error creating instance: VcpuLimitExceeded: quota exceeded", "할당량"),
        ("AddressLimitExceeded: Too many addresses allocated", "IP"),
        ("AccessDenied: User is not authorized to perform: ec2:RunInstances", "권한"),
        ("InvalidClientTokenId: The security token included in the request is invalid", "재검증"),
        ("Error: master password does not conform to policy requirements", "비밀번호"),
        ("InvalidSubnetID.NotFound: subnet not found", "네트워크"),
        ("InvalidAMIID.NotFound: ami does not exist", "이미지"),
        ("Error: context deadline exceeded (timeout)", "시간 초과"),
        ("Unsupported region: this region is not enabled for your account", "리전"),
    ],
)
def test_common_patterns_translated(raw, expect):
    msg = translate_reason(raw)
    assert msg is not None, f"매칭 실패: {raw!r}"
    assert expect in msg


def test_sku_not_available_and_quota_exceeded_get_different_messages():
    """2026-09-18 회귀: Azure VM 생성 실패의 두 원인(SkuNotAvailable/Capacity Restrictions vs
    vCPU 할당량 초과)은 해결책이 다르므로 서로 다른 한글 문구로 번역돼야 한다 — 하나로
    뭉뚱그리면 "할당량을 늘리면 된다"는 잘못된 조치를 안내하게 된다(실측: koreacentral/eastus의
    B1s/B2s/D2s_v3에서 SkuNotAvailable을 실제로 겪었으나 이건 할당량 문제가 아니었다)."""
    sku_msg = translate_reason(
        "Error: creating Linux Virtual Machine: SkuNotAvailable: The requested VM size for "
        "resource 'Following SKUs have failed for Capacity Restrictions: Standard_B1s' is "
        "currently not available in location 'koreacentral'."
    )
    quota_msg = translate_reason(
        "Error: creating Linux Virtual Machine: OperationNotAllowed: Operation could not be "
        "completed as it results in exceeding approved standardBSFamily Cores quota."
    )

    assert sku_msg is not None and quota_msg is not None
    assert sku_msg != quota_msg
    assert "SKU" in sku_msg and "할당량" not in sku_msg
    assert "할당량" in quota_msg
    # 무료 체험 구독이 할당량 증설 대상이 아닐 수 있다는 안내는 quota 쪽에만 있어야 한다
    # (SkuNotAvailable의 원인 전부가 할당량 문제라고 암시하지 않기 위함).
    assert "무료 체험" in quota_msg
    assert "무료 체험" not in sku_msg


def test_sku_not_available_pattern_does_not_shadow_password_or_busy_errors():
    """SkuNotAvailable/quota 패턴이 너무 넓게 잡히면 비밀번호 복잡도 오류나 실행 환경 오류
    (`text file busy`)까지 잘못 흡수할 수 있다 — 그러면 안 된다."""
    pw_msg = translate_reason("Error: master password does not conform to policy requirements")
    assert pw_msg is not None and "SKU" not in pw_msg and "할당량" not in pw_msg

    busy_msg = translate_reason("fork/exec /usr/local/bin/terraform: text file busy")
    assert busy_msg is None or ("SKU" not in busy_msg and "할당량" not in busy_msg)


def test_matching_is_case_insensitive():
    assert translate_reason("ACCESSDENIED: not authorized") is not None


def test_no_match_returns_none():
    assert translate_reason("Error: some totally unclassified failure blah blah") is None


def test_empty_and_none_return_none():
    assert translate_reason(None) is None
    assert translate_reason("") is None
