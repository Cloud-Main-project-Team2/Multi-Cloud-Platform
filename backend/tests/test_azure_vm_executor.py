"""app.azure_provisioning 단위 테스트(통합 flat 계약).

통합 후 Azure 러너는 `validate_spec()` + `run(...) -> TerraformResult` 두 함수만 제공하고
DB·감사·상태 전이는 라우터가 처리한다. 여기서는 실제 terraform 대신 `run_apply`를 가짜로
바꿔치기해 (1) tfvars에 admin_password가 없고 (2) env로만 넘어가며 (3) 검증/성공/실패 분기가
맞는지만 검증한다. DB를 거치는 종단 흐름은 test_provisioning_azure_vm.py에서 다룬다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import azure_provisioning as azure
from app.errors import ApiError
from app.terraform_runner import TerraformResult

COMMON_SPEC = {
    "name": "web-01",
    "tags": {"env": "test"},
    "inbound_rules": [{"port": 22, "cidr": "0.0.0.0/0"}],
}
PROVIDER_SPEC = {
    "region": "koreacentral",
    "instance_type": "B1s",
    "admin_username": "azureuser",
    "admin_password": "S3curePassw0rd!",
    "image": "Ubuntu 22.04",
}
SECRET_PAYLOAD = {
    "tenant_id": "tenant-1234",
    "client_id": "client-1234",
    "client_secret": "s3cr3t-value-long",
}
# 구독 ID는 secret_payload가 아니라 cloud_accounts.external_account_id에서 오고, 라우터가
# project_id로 넘긴다(2026-09-15 수정 — run() 참고).
PROJECT_ID = "subscription-1234"


def test_run_success_passes_credentials_via_env_not_tfvars(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, secrets=None, cancel_check=None, **_):
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        captured["secrets"] = secrets
        return TerraformResult(success=True, outputs={"vm_id": "fake-vm-id", "public_ip_address": "1.2.3.4"})

    monkeypatch.setattr(azure, "run_apply", fake_run_apply)

    result = azure.run(
        job_id=1,
        workspace_dir=tmp_path / "ws",
        common_spec=COMMON_SPEC,
        provider_spec=PROVIDER_SPEC,
        secret_payload=SECRET_PAYLOAD,
        project_id=PROJECT_ID,
    )

    assert result.success is True
    assert result.outputs["vm_id"] == "fake-vm-id"

    tfvars = captured["tfvars"]
    assert "admin_password" not in tfvars  # tfvars 파일(디스크)엔 절대 안 쓴다
    assert tfvars["vm_size"] == "Standard_B1s"  # "B1s" -> Standard_ 접두사 자동 보정
    assert tfvars["inbound_rules"] == [{"port": 22, "cidr": "0.0.0.0/0"}]
    assert tfvars["tags"]["env"] == "test"

    env = captured["credential_env"]
    assert env["ARM_CLIENT_SECRET"] == "s3cr3t-value-long"
    assert env["ARM_SUBSCRIPTION_ID"] == "subscription-1234"
    assert env["TF_VAR_admin_password"] == "S3curePassw0rd!"
    # 실패 메시지 redact 대상에 client_secret과 admin_password가 포함돼야 한다.
    assert "s3cr3t-value-long" in captured["secrets"]
    assert "S3curePassw0rd!" in captured["secrets"]


def test_run_missing_secret_field_fails_without_calling_terraform(monkeypatch, tmp_path):
    def _unexpected(*args, **kwargs):
        raise AssertionError("credential이 불완전하면 terraform을 호출해서는 안 된다")

    monkeypatch.setattr(azure, "run_apply", _unexpected)

    result = azure.run(
        job_id=1,
        workspace_dir=tmp_path / "ws",
        common_spec=COMMON_SPEC,
        provider_spec=PROVIDER_SPEC,
        secret_payload={"tenant_id": "t-long-enough", "client_id": "c"},  # client_secret 없음
        project_id=PROJECT_ID,
    )

    assert result.success is False
    assert result.error_code == "PROVIDER_AUTHENTICATION_FAILED"


def test_run_missing_project_id_fails_even_with_full_secret(monkeypatch, tmp_path):
    # 회귀 테스트(2026-09-15) — secret_payload는 완전해도 project_id(구독 ID)가 안 넘어오면
    # 실패해야 한다. 마이페이지 UI 경로에서 실제로 겪었던 버그(구독 ID가 secret_payload가
    # 아니라 cloud_accounts.external_account_id에만 저장됨)를 고정한다.
    def _unexpected(*args, **kwargs):
        raise AssertionError("project_id 없으면 terraform을 호출해서는 안 된다")

    monkeypatch.setattr(azure, "run_apply", _unexpected)

    result = azure.run(
        job_id=1,
        workspace_dir=tmp_path / "ws",
        common_spec=COMMON_SPEC,
        provider_spec=PROVIDER_SPEC,
        secret_payload=SECRET_PAYLOAD,
        project_id=None,
    )

    assert result.success is False
    assert result.error_code == "PROVIDER_AUTHENTICATION_FAILED"
    assert "subscription_id" in result.error_message


def test_run_propagates_terraform_failure(monkeypatch, tmp_path):
    def fake_run_apply(*args, **kwargs):
        return TerraformResult(success=False, error_code="CLOUD_PERMISSION_DENIED", error_message="denied")

    monkeypatch.setattr(azure, "run_apply", fake_run_apply)

    result = azure.run(
        job_id=1,
        workspace_dir=tmp_path / "ws",
        common_spec=COMMON_SPEC,
        provider_spec=PROVIDER_SPEC,
        secret_payload=SECRET_PAYLOAD,
        project_id=PROJECT_ID,
    )

    assert result.success is False
    assert result.error_code == "CLOUD_PERMISSION_DENIED"


def test_validate_spec_rejects_short_admin_password():
    with pytest.raises(ApiError) as exc:
        azure.validate_spec(COMMON_SPEC, {**PROVIDER_SPEC, "admin_password": "short"})
    assert exc.value.code == "VALIDATION_ERROR"


def test_validate_spec_rejects_low_complexity_admin_password():
    """2026-09-18 실측: 실제 자격증명으로 프로비저닝했을 때 길이는 충분한데 복잡도(대/소문자/
    숫자/특수문자 중 3종 이상)가 2종뿐인 비밀번호가 요청 단계(422)가 아니라 몇 분 뒤 terraform
    apply 실패로 처음 드러났다 — 소문자+숫자 2종만 있는 비밀번호를 미리 걸러내는지 검증."""
    with pytest.raises(ApiError) as exc:
        azure.validate_spec(COMMON_SPEC, {**PROVIDER_SPEC, "admin_password": "onlylowerand12345"})
    assert exc.value.code == "VALIDATION_ERROR"


def test_validate_spec_accepts_password_with_underscore_as_third_class():
    """밑줄(_)은 Azure 실제 정책상 "특수문자"로 안 쳐준다 — 소문자+숫자+밑줄만 있는 비밀번호는
    여전히 2종 취급으로 거부돼야 한다(3종 채우려면 대문자나 _ 아닌 특수문자가 필요)."""
    with pytest.raises(ApiError) as exc:
        azure.validate_spec(COMMON_SPEC, {**PROVIDER_SPEC, "admin_password": "lower_and_1234567"})
    assert exc.value.code == "VALIDATION_ERROR"


def test_validate_spec_rejects_missing_common_name():
    with pytest.raises(ApiError) as exc:
        azure.validate_spec({}, PROVIDER_SPEC)
    assert exc.value.code == "VALIDATION_ERROR"


def test_instance_type_without_standard_prefix_is_normalized():
    spec = azure.ProviderSpec.model_validate({**PROVIDER_SPEC, "instance_type": "B2s"})
    assert spec.instance_type == "Standard_B2s"

    already_prefixed = azure.ProviderSpec.model_validate({**PROVIDER_SPEC, "instance_type": "Standard_B2s"})
    assert already_prefixed.instance_type == "Standard_B2s"


def test_run_uses_workspace_name_for_resource_group(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, **_):
        captured["rg"] = tfvars["resource_group_name"]
        return TerraformResult(success=True, outputs={})

    monkeypatch.setattr(azure, "run_apply", fake_run_apply)

    azure.run(
        job_id=7,
        workspace_dir=Path(tmp_path) / "user-1-job-7",
        common_spec=COMMON_SPEC,
        provider_spec=PROVIDER_SPEC,
        secret_payload=SECRET_PAYLOAD,
        project_id=PROJECT_ID,
    )
    assert captured["rg"] == "rg-user-1-job-7"


def test_validate_spec_accepts_existing_resource_fields():
    azure.validate_spec(
        COMMON_SPEC,
        {
            **PROVIDER_SPEC,
            "existing_resource_group_name": "my-existing-rg",
            "existing_subnet_id": "/subscriptions/x/resourceGroups/my-existing-rg/providers/Microsoft.Network/virtualNetworks/vnet1/subnets/subnet1",
            "existing_network_security_group_id": "/subscriptions/x/resourceGroups/my-existing-rg/providers/Microsoft.Network/networkSecurityGroups/nsg1",
        },
    )


def test_run_passes_existing_resource_fields_through_to_tfvars(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, **_):
        captured["tfvars"] = tfvars
        return TerraformResult(success=True, outputs={})

    monkeypatch.setattr(azure, "run_apply", fake_run_apply)

    azure.run(
        job_id=7,
        workspace_dir=Path(tmp_path) / "user-1-job-7",
        common_spec=COMMON_SPEC,
        provider_spec={**PROVIDER_SPEC, "existing_resource_group_name": "my-existing-rg"},
        secret_payload=SECRET_PAYLOAD,
        project_id=PROJECT_ID,
    )
    assert captured["tfvars"]["existing_resource_group_name"] == "my-existing-rg"
    assert captured["tfvars"]["existing_subnet_id"] is None
    assert captured["tfvars"]["existing_network_security_group_id"] is None
