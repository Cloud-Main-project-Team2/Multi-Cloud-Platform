"""`app/aws_s3_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, credential -> env 변환."""

from __future__ import annotations

import pytest

from app import aws_s3_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "assets-bucket"}
VALID_PROVIDER = {"region": "ap-northeast-2"}


def test_validate_spec_accepts_valid_input():
    aws_s3_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 정규식은 최소 2자
        (VALID_COMMON, {"region": "eu-west-1"}),
        (VALID_COMMON, {"region": "ap-northeast-2", "unexpected": "field"}),
        ({"name": "assets-bucket", "unexpected": "field"}, VALID_PROVIDER),
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        aws_s3_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_generates_globally_unique_bucket_name():
    common, provider = aws_s3_provisioning._derive(VALID_COMMON, VALID_PROVIDER)
    tfvars = aws_s3_provisioning.build_tfvars(42, common, provider)
    assert tfvars["bucket_name"] == "mcp-assets-bucket-42"
    assert tfvars["region"] == "ap-northeast-2"
    assert tfvars["versioning_enabled"] is False
    assert tfvars["tags"]["job-id"] == "42"
    assert tfvars["tags"]["managed-by"] == "multi-cloud-platform"


def test_build_tfvars_merges_user_tags_and_versioning():
    common, provider = aws_s3_provisioning._derive(
        {"name": "assets-bucket", "tags": {"env": "prod"}},
        {"region": "us-east-1", "versioning_enabled": True},
    )
    tfvars = aws_s3_provisioning.build_tfvars(7, common, provider)
    assert tfvars["tags"]["env"] == "prod"
    assert tfvars["versioning_enabled"] is True


def test_run_calls_run_apply_with_credential_env(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, cancel_check=None):
        captured["workspace_dir"] = workspace_dir
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        return TerraformResult(success=True, outputs={"bucket_name": "mcp-assets-bucket-1"})

    monkeypatch.setattr(aws_s3_provisioning, "run_apply", fake_run_apply)

    result = aws_s3_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE", "secret_access_key": "shh", "session_token": "tok"},
    )

    assert result.success is True
    assert captured["tfvars"]["bucket_name"] == "mcp-assets-bucket-1"
    assert captured["credential_env"] == {
        "AWS_ACCESS_KEY_ID": "AKIAFAKE",
        "AWS_SECRET_ACCESS_KEY": "shh",
        "AWS_SESSION_TOKEN": "tok",
    }


def test_run_returns_failed_result_when_credential_incomplete(tmp_path):
    result = aws_s3_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE"},  # secret_access_key 누락
    )
    assert result.success is False
    assert result.error_code == "CREDENTIAL_VERIFICATION_FAILED"


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = aws_s3_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec={"name": "Bad_Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE", "secret_access_key": "shh"},
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"
