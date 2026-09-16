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


def test_matching_is_case_insensitive():
    assert translate_reason("ACCESSDENIED: not authorized") is not None


def test_no_match_returns_none():
    assert translate_reason("Error: some totally unclassified failure blah blah") is None


def test_empty_and_none_return_none():
    assert translate_reason(None) is None
    assert translate_reason("") is None
