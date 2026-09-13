"""`app/gcp_cloudsql_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, run() 위임."""

from __future__ import annotations

import pytest

from app import gcp_cloudsql_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "web-db"}
VALID_PROVIDER = {"region": "asia-northeast3", "engine": "MySQL", "master_password": "S3curePassw0rd!"}


def test_validate_spec_accepts_valid_input():
    gcp_cloudsql_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 정규식은 최소 2자
        (VALID_COMMON, {**VALID_PROVIDER, "region": "eu-west-1"}),
        (VALID_COMMON, {**VALID_PROVIDER, "engine": "Oracle"}),  # GCP 허용 목록에 없음
        (VALID_COMMON, {**VALID_PROVIDER, "master_password": "short"}),
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        gcp_cloudsql_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "engine,expected_version,expected_tier,expected_admin_user,expected_create_admin_user",
    [
        ("MySQL", "MYSQL_8_0", "db-f1-micro", "root", True),
        ("PostgreSQL", "POSTGRES_15", "db-custom-1-3840", "postgres", True),
        ("SQL Server", "SQLSERVER_2019_EXPRESS", "db-custom-1-3840", "sqlserver", False),
    ],
)
def test_build_tfvars_uses_per_engine_config(
    engine, expected_version, expected_tier, expected_admin_user, expected_create_admin_user
):
    engine_config = gcp_cloudsql_provisioning._ENGINE_CONFIG[engine]
    tfvars = gcp_cloudsql_provisioning.build_tfvars(42, "proj-1", "mcp-web-db", "asia-northeast3", engine_config)

    assert tfvars["instance_name"] == "mcp-web-db"
    assert tfvars["database_version"] == expected_version
    assert tfvars["tier"] == expected_tier
    assert tfvars["admin_user"] == expected_admin_user
    assert tfvars["create_admin_user"] is expected_create_admin_user
    assert tfvars["labels"]["job-id"] == "42"
    assert "root_password" not in tfvars  # tfvars 파일에는 절대 안 넣는다


def test_run_calls_run_apply_with_credentials_file_and_password_env(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, credentials_file=None, secrets=None, cancel_check=None):
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        captured["credentials_file"] = credentials_file
        captured["secrets"] = secrets
        return TerraformResult(success=True, outputs={"instance_name": "mcp-web-db"})

    monkeypatch.setattr(gcp_cloudsql_provisioning, "run_apply", fake_run_apply)

    result = gcp_cloudsql_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"type": "service_account", "token_uri": "https://oauth2.googleapis.com/token"},
    )

    assert result.success is True
    assert captured["tfvars"]["instance_name"] == "mcp-web-db"
    assert captured["credential_env"] == {"TF_VAR_root_password": "S3curePassw0rd!"}
    assert captured["credentials_file"] == {"type": "service_account", "token_uri": "https://oauth2.googleapis.com/token"}
    assert captured["secrets"] == ["S3curePassw0rd!"]


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = gcp_cloudsql_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec={"name": "Bad_Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload={"type": "service_account"},
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"
