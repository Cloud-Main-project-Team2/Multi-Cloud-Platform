"""`app/aws_rds_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, credential -> env 변환."""

from __future__ import annotations

import pytest

from app import aws_rds_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "orders-db"}
VALID_PROVIDER = {"region": "ap-northeast-2", "engine": "postgres", "master_password": "Sup3r-Secret!"}


def test_validate_spec_accepts_valid_input():
    aws_rds_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 정규식은 최소 2자
        (VALID_COMMON, {**VALID_PROVIDER, "region": "eu-west-1"}),
        (VALID_COMMON, {**VALID_PROVIDER, "engine": "oracle"}),
        (VALID_COMMON, {**VALID_PROVIDER, "instance_class": "db.m5.24xlarge"}),
        (VALID_COMMON, {**VALID_PROVIDER, "master_password": "short1"}),  # 8자 미만
        (VALID_COMMON, {**VALID_PROVIDER, "master_password": "has a space1"}),  # 공백 금지
        (VALID_COMMON, {**VALID_PROVIDER, "master_password": 'has"quote1'}),  # 큰따옴표 금지
        (VALID_COMMON, {**VALID_PROVIDER, "unexpected": "field"}),
        ({"name": "orders-db", "unexpected": "field"}, VALID_PROVIDER),
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        aws_rds_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_derives_identifier_and_db_name():
    common, provider = aws_rds_provisioning._derive(VALID_COMMON, VALID_PROVIDER)
    tfvars = aws_rds_provisioning.build_tfvars(42, common, provider)
    assert tfvars["instance_name"] == "mcp-orders-db"
    assert tfvars["db_name"] == "orders_db"  # postgres db_name엔 하이픈을 못 써서 밑줄로 치환
    assert tfvars["engine"] == "postgres"
    assert tfvars["instance_class"] == "db.t3.micro"  # 기본값
    assert "master_password" not in tfvars  # tfvars 파일에는 절대 넣지 않는다
    assert tfvars["tags"]["job-id"] == "42"
    assert tfvars["tags"]["managed-by"] == "multi-cloud-platform"


def test_build_tfvars_merges_user_tags_and_custom_instance_class():
    common, provider = aws_rds_provisioning._derive(
        {"name": "orders-db", "tags": {"env": "prod"}},
        {**VALID_PROVIDER, "instance_class": "db.t3.small"},
    )
    tfvars = aws_rds_provisioning.build_tfvars(7, common, provider)
    assert tfvars["tags"]["env"] == "prod"
    assert tfvars["instance_class"] == "db.t3.small"


def test_run_calls_run_apply_with_credential_env_and_password_via_tfvar(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, secrets=None, cancel_check=None):
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        captured["secrets"] = secrets
        return TerraformResult(success=True, outputs={"db_instance_id": "mcp-orders-db"})

    monkeypatch.setattr(aws_rds_provisioning, "run_apply", fake_run_apply)

    result = aws_rds_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE", "secret_access_key": "shh"},
    )

    assert result.success is True
    assert captured["credential_env"]["AWS_ACCESS_KEY_ID"] == "AKIAFAKE"
    assert captured["credential_env"]["TF_VAR_master_password"] == "Sup3r-Secret!"
    assert "master_password" not in captured["tfvars"]
    assert captured["secrets"] == ["Sup3r-Secret!"]


def test_run_returns_failed_result_when_credential_incomplete(tmp_path):
    result = aws_rds_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE"},  # secret_access_key 누락
    )
    assert result.success is False
    assert result.error_code == "CREDENTIAL_VERIFICATION_FAILED"


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = aws_rds_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec={"name": "Bad_Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload={"access_key_id": "AKIAFAKE", "secret_access_key": "shh"},
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"
