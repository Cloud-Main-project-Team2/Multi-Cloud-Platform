"""`app/azure_database_provisioning.py` 단위 검증 — spec 허용 목록, 엔진별 모듈 선택,
tfvars 조립, run() 위임."""

from __future__ import annotations

import pytest

from app import azure_database_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "web-db"}
VALID_PROVIDER = {
    "region": "koreacentral",
    "engine": "MySQL",
    "master_username": "dbadmin",
    "master_password": "S3cure!Pass",
}
VALID_SECRET = {
    "tenant_id": "tenant-1",
    "client_id": "client-1",
    "client_secret": "supersecretvalue",
}
# 구독 ID는 secret_payload가 아니라 cloud_accounts.external_account_id에서 오고, 라우터가
# project_id로 넘긴다(2026-09-15 수정 — _credential_env() 참고).
VALID_PROJECT_ID = "sub-1"


def test_validate_spec_accepts_valid_input():
    azure_database_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize("engine", ["MySQL", "PostgreSQL", "SQL Server"])
def test_validate_spec_accepts_all_three_engines(engine):
    azure_database_provisioning.validate_spec(VALID_COMMON, {**VALID_PROVIDER, "engine": engine})


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 최소 2자
        (VALID_COMMON, {**VALID_PROVIDER, "region": "eu-west-1"}),
        (VALID_COMMON, {**VALID_PROVIDER, "engine": "Oracle"}),  # 허용 목록에 없음
        (VALID_COMMON, {**VALID_PROVIDER, "master_username": "admin"}),  # 예약어
        (VALID_COMMON, {**VALID_PROVIDER, "master_username": "1baduser"}),  # 숫자로 시작 불가
        (VALID_COMMON, {**VALID_PROVIDER, "master_password": "short1!"}),  # 8자 미만
        (VALID_COMMON, {**VALID_PROVIDER, "master_password": "alllowercase1"}),  # 복잡도 2종뿐
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        azure_database_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "engine,expected_module",
    [("MySQL", "mysql"), ("PostgreSQL", "postgresql"), ("SQL Server", "sql_server")],
)
def test_build_tfvars_selects_correct_module_per_engine(engine, expected_module):
    common, provider = azure_database_provisioning._derive(VALID_COMMON, {**VALID_PROVIDER, "engine": engine})
    tfvars, module_dir = azure_database_provisioning.build_tfvars(42, "user-1-job-42", common, provider)

    assert module_dir.name == expected_module
    assert tfvars["server_name"] == "mcp-web-db-42"
    assert tfvars["resource_group_name"] == "rg-user-1-job-42"
    assert tfvars["database_name"] == "web_db"  # 하이픈 -> 밑줄
    assert tfvars["admin_login"] == "dbadmin"
    assert tfvars["tags"]["job-id"] == "42"
    assert "admin_password" not in tfvars  # tfvars 파일에는 절대 안 넣는다


def test_build_tfvars_truncates_server_name_to_63_chars():
    common, provider = azure_database_provisioning._derive({"name": "x" * 40}, VALID_PROVIDER)
    tfvars, _ = azure_database_provisioning.build_tfvars(999999, "ws", common, provider)
    assert len(tfvars["server_name"]) <= 63


def test_build_tfvars_defaults_existing_resource_fields_to_none():
    common, provider = azure_database_provisioning._derive(VALID_COMMON, VALID_PROVIDER)
    tfvars, _ = azure_database_provisioning.build_tfvars(42, "ws", common, provider)
    assert tfvars["existing_resource_group_name"] is None
    assert tfvars["existing_vnet_id"] is None
    assert tfvars["existing_vnet_subnet_cidr"] is None


def test_validate_spec_accepts_existing_resource_fields():
    azure_database_provisioning.validate_spec(
        VALID_COMMON,
        {
            **VALID_PROVIDER,
            "existing_resource_group_name": "my-existing-rg",
            "existing_vnet_id": "/subscriptions/x/resourceGroups/my-existing-rg/providers/Microsoft.Network/virtualNetworks/vnet1",
            "existing_vnet_subnet_cidr": "10.5.1.0/24",
        },
    )


def test_build_tfvars_includes_existing_resource_fields_when_given():
    common, provider = azure_database_provisioning._derive(
        VALID_COMMON, {**VALID_PROVIDER, "existing_resource_group_name": "my-existing-rg", "existing_vnet_id": "vnet-arm-id"}
    )
    tfvars, _ = azure_database_provisioning.build_tfvars(42, "ws", common, provider)
    assert tfvars["existing_resource_group_name"] == "my-existing-rg"
    assert tfvars["existing_vnet_id"] == "vnet-arm-id"


def test_run_calls_run_apply_with_arm_credential_env_and_password_var(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, secrets=None, cancel_check=None):
        captured["module_dir"] = module_dir
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        captured["secrets"] = secrets
        return TerraformResult(success=True, outputs={"server_name": tfvars["server_name"]})

    monkeypatch.setattr(azure_database_provisioning, "run_apply", fake_run_apply)

    result = azure_database_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        workspace_name="user-1-job-1",
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload=VALID_SECRET,
        project_id=VALID_PROJECT_ID,
    )

    assert result.success is True
    assert captured["module_dir"].name == "mysql"
    assert captured["credential_env"]["ARM_TENANT_ID"] == "tenant-1"
    assert captured["credential_env"]["ARM_SUBSCRIPTION_ID"] == VALID_PROJECT_ID
    assert captured["credential_env"]["TF_VAR_admin_password"] == "S3cure!Pass"
    assert "S3cure!Pass" in captured["secrets"]


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = azure_database_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec={"name": "Bad_Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload=VALID_SECRET,
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"


def test_run_returns_failed_result_on_missing_credential_fields(tmp_path):
    result = azure_database_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"tenant_id": "tenant-1"},
        project_id=VALID_PROJECT_ID,
    )
    assert result.success is False
    assert result.error_code == "CREDENTIAL_VERIFICATION_FAILED"


def test_run_returns_failed_result_when_project_id_missing_even_with_full_secret(tmp_path):
    # 회귀 테스트(2026-09-15) — secret_payload는 완전해도 project_id(구독 ID)가 안 넘어오면
    # 실패해야 한다. 마이페이지 UI 경로에서 실제로 겪었던 버그를 고정한다.
    result = azure_database_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload=VALID_SECRET,
        project_id=None,
    )
    assert result.success is False
    assert result.error_code == "CREDENTIAL_VERIFICATION_FAILED"
    assert "subscription_id" in result.error_message
