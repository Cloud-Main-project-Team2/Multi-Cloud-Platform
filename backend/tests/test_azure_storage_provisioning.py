"""`app/azure_storage_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, run() 위임."""

from __future__ import annotations

import pytest

from app import azure_storage_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "myuniquestorage01"}
VALID_PROVIDER = {"region": "koreacentral"}
VALID_SECRET = {
    "tenant_id": "tenant-1",
    "client_id": "client-1",
    "client_secret": "supersecretvalue",
    "subscription_id": "sub-1",
}


def test_validate_spec_accepts_valid_input():
    azure_storage_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "AB-storage"}, VALID_PROVIDER),  # 대문자/하이픈 불가
        ({"name": "a"}, VALID_PROVIDER),  # 최소 2자
        ({"name": "x" * 22}, VALID_PROVIDER),  # 최대 21자 초과
        (VALID_COMMON, {**VALID_PROVIDER, "region": "eu-west-1"}),  # 허용 목록에 없음
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        azure_storage_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_uses_hyphen_free_account_name():
    common, provider = azure_storage_provisioning._derive(VALID_COMMON, VALID_PROVIDER)
    tfvars = azure_storage_provisioning.build_tfvars(42, "user-1-job-42", common, provider)

    assert tfvars["account_name"] == "mcpmyuniquestorage0142"  # 하이픈 없이 이어붙임
    assert tfvars["resource_group_name"] == "rg-user-1-job-42"
    assert tfvars["location"] == "koreacentral"
    assert tfvars["tags"]["job-id"] == "42"


def test_build_tfvars_truncates_account_name_to_24_chars():
    common, provider = azure_storage_provisioning._derive({"name": "x" * 21}, VALID_PROVIDER)
    tfvars = azure_storage_provisioning.build_tfvars(999999, "ws", common, provider)

    assert len(tfvars["account_name"]) == 24
    assert tfvars["account_name"] == ("mcp" + "x" * 21)[:24]


def test_run_calls_run_apply_with_arm_credential_env(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, secrets=None, cancel_check=None):
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        captured["secrets"] = secrets
        return TerraformResult(success=True, outputs={"account_name": tfvars["account_name"]})

    monkeypatch.setattr(azure_storage_provisioning, "run_apply", fake_run_apply)

    result = azure_storage_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        workspace_name="user-1-job-1",
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload=VALID_SECRET,
    )

    assert result.success is True
    assert captured["credential_env"] == {
        "ARM_TENANT_ID": "tenant-1",
        "ARM_CLIENT_ID": "client-1",
        "ARM_CLIENT_SECRET": "supersecretvalue",
        "ARM_SUBSCRIPTION_ID": "sub-1",
    }
    assert captured["secrets"] == ["supersecretvalue", "tenant-1", "client-1"]


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = azure_storage_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec={"name": "Bad-Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload=VALID_SECRET,
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"


def test_run_returns_failed_result_on_missing_credential_fields(tmp_path):
    result = azure_storage_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"tenant_id": "tenant-1"},  # client_id/client_secret/subscription_id 없음
    )
    assert result.success is False
    assert result.error_code == "CREDENTIAL_VERIFICATION_FAILED"
