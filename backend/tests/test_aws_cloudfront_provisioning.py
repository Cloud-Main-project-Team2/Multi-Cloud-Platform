"""`app/aws_cloudfront_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, credential -> env 변환."""

from __future__ import annotations

import pytest

from app import aws_cloudfront_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "assets-cdn"}
VALID_PROVIDER = {"origin_domain_name": "example-bucket.s3.ap-northeast-2.amazonaws.com"}


def test_validate_spec_accepts_valid_input():
    aws_cloudfront_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 정규식은 최소 2자
        (VALID_COMMON, {"origin_domain_name": "https://example.com"}),  # 스킴 금지
        (VALID_COMMON, {"origin_domain_name": "example.com/path"}),  # 경로 금지
        (VALID_COMMON, {"origin_domain_name": "not a domain"}),
        (VALID_COMMON, {"origin_domain_name": "example.com", "unexpected": "field"}),
        ({"name": "assets-cdn", "unexpected": "field"}, VALID_PROVIDER),
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        aws_cloudfront_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_uses_name_as_distribution_comment():
    common, provider = aws_cloudfront_provisioning._derive(VALID_COMMON, VALID_PROVIDER)
    tfvars = aws_cloudfront_provisioning.build_tfvars(42, common, provider)
    assert tfvars["distribution_name"] == "assets-cdn"
    assert tfvars["origin_domain_name"] == "example-bucket.s3.ap-northeast-2.amazonaws.com"
    assert tfvars["tags"]["job-id"] == "42"
    assert tfvars["tags"]["managed-by"] == "multi-cloud-platform"


def test_build_tfvars_merges_user_tags():
    common, provider = aws_cloudfront_provisioning._derive(
        {"name": "assets-cdn", "tags": {"env": "prod"}}, VALID_PROVIDER
    )
    tfvars = aws_cloudfront_provisioning.build_tfvars(7, common, provider)
    assert tfvars["tags"]["env"] == "prod"


def test_run_calls_run_apply_with_credential_env(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, cancel_check=None):
        captured["workspace_dir"] = workspace_dir
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        return TerraformResult(success=True, outputs={"distribution_id": "E1234567890"})

    monkeypatch.setattr(aws_cloudfront_provisioning, "run_apply", fake_run_apply)

    result = aws_cloudfront_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE", "secret_access_key": "shh", "session_token": "tok"},
    )

    assert result.success is True
    assert captured["tfvars"]["distribution_name"] == "assets-cdn"
    assert captured["credential_env"] == {
        "AWS_ACCESS_KEY_ID": "AKIAFAKE",
        "AWS_SECRET_ACCESS_KEY": "shh",
        "AWS_SESSION_TOKEN": "tok",
    }


def test_run_returns_failed_result_when_credential_incomplete(tmp_path):
    result = aws_cloudfront_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE"},  # secret_access_key 누락
    )
    assert result.success is False
    assert result.error_code == "CREDENTIAL_VERIFICATION_FAILED"


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = aws_cloudfront_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec={"name": "Bad_Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE", "secret_access_key": "shh"},
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"
