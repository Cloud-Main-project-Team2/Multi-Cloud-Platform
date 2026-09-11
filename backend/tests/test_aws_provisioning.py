"""`app/aws_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, credential -> env 변환."""

from __future__ import annotations

import pytest

from app import aws_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "web-01"}
VALID_PROVIDER = {"region": "ap-northeast-2", "instance_type": "t3.micro"}


def test_validate_spec_accepts_valid_input():
    aws_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 정규식은 최소 2자
        (VALID_COMMON, {"region": "ap-northeast-2", "instance_type": "m5.24xlarge"}),
        (VALID_COMMON, {"region": "eu-west-1", "instance_type": "t3.micro"}),
        (VALID_COMMON, {"region": "ap-northeast-2", "instance_type": "t3.micro", "ami_id": "not-an-ami"}),
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        aws_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_uses_mcp_prefixed_name_and_job_tag():
    tfvars = aws_provisioning.build_tfvars(42, "mcp-web-01", "ap-northeast-2", "t3.micro", None)
    assert tfvars["instance_name"] == "mcp-web-01"
    assert tfvars["ami_id"] is None
    assert tfvars["tags"]["job-id"] == "42"


def test_run_calls_run_apply_with_credential_env(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, cancel_check=None):
        captured["workspace_dir"] = workspace_dir
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        return TerraformResult(success=True, outputs={"instance_id": "i-123"})

    monkeypatch.setattr(aws_provisioning, "run_apply", fake_run_apply)

    result = aws_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE", "secret_access_key": "shh", "session_token": "tok"},
    )

    assert result.success is True
    assert captured["tfvars"]["instance_name"] == "mcp-web-01"
    assert captured["credential_env"] == {
        "AWS_ACCESS_KEY_ID": "AKIAFAKE",
        "AWS_SECRET_ACCESS_KEY": "shh",
        "AWS_SESSION_TOKEN": "tok",
    }


def test_run_returns_failed_result_when_credential_incomplete(tmp_path):
    result = aws_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE"},  # secret_access_key 누락
    )
    assert result.success is False
    assert result.error_code == "CREDENTIAL_VERIFICATION_FAILED"


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = aws_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec={"name": "Bad_Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE", "secret_access_key": "shh"},
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"
