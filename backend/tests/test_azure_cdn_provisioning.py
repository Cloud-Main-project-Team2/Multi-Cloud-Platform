"""`app/azure_cdn_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, run() 위임.

다른 Azure 러너(VM/Storage Account/Database)와 달리 `resource_group`은 서버 자동생성이
기본값(빈 경우 `rg-cdn-{workspace_name}`)이지만, 프론트가 이미 필수 입력으로 받고 있는 값을
그대로 새 리소스 그룹 이름으로 쓸 수 있다(2026-09-15 결정 — `azure_cdn_provisioning.py`
모듈 docstring 참고).
"""

from __future__ import annotations

import pytest

from app import azure_cdn_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "cdn-01"}
VALID_PROVIDER = {"origin": "example.z13.web.core.windows.net", "sku": "Standard"}
VALID_SECRET = {
    "tenant_id": "tenant-1",
    "client_id": "client-1",
    "client_secret": "shh",
}
# 구독 ID는 secret_payload가 아니라 cloud_accounts.external_account_id에서 오고, 라우터가
# project_id로 넘긴다(2026-09-15 수정 — _credential_env() 참고).
VALID_PROJECT_ID = "sub-1"


def test_validate_spec_accepts_minimal_valid_input():
    azure_cdn_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


def test_validate_spec_accepts_full_input_with_custom_resource_group():
    azure_cdn_provisioning.validate_spec(
        VALID_COMMON,
        {
            **VALID_PROVIDER,
            "resource_group": "rg-my-cdn",
            "query_string_caching_behavior": "UseQueryString",
            "protocol": "https_only",
            "health_probe_path": "/healthz",
            "health_probe_interval_seconds": 30,
            "compression": False,
            "https_redirect": False,
        },
    )


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 정규식은 최소 2자
        (VALID_COMMON, {"origin": "not a hostname", "sku": "Standard"}),
        (VALID_COMMON, {"origin": "example.com"}),  # sku 누락
        (VALID_COMMON, {**VALID_PROVIDER, "sku": "Premium"}),  # 비용 문제로 Standard만 허용
        (VALID_COMMON, {**VALID_PROVIDER, "resource_group": "bad.rg."}),  # 마침표로 끝남
        (VALID_COMMON, {**VALID_PROVIDER, "query_string_caching_behavior": "Bogus"}),
        (VALID_COMMON, {**VALID_PROVIDER, "protocol": "quic"}),
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        azure_cdn_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_uses_frontend_resource_group_when_present():
    common, provider = azure_cdn_provisioning._derive(
        VALID_COMMON, {**VALID_PROVIDER, "resource_group": "rg-my-cdn"}
    )
    tfvars = azure_cdn_provisioning.build_tfvars(1, "user-1-job-1", common, provider)
    assert tfvars["resource_group_name"] == "rg-my-cdn"


def test_build_tfvars_auto_generates_resource_group_when_absent():
    common, provider = azure_cdn_provisioning._derive(VALID_COMMON, VALID_PROVIDER)
    tfvars = azure_cdn_provisioning.build_tfvars(1, "user-1-job-1", common, provider)
    assert tfvars["resource_group_name"] == "rg-cdn-user-1-job-1"
    assert tfvars["use_existing_resource_group"] is False


def test_validate_spec_accepts_use_existing_resource_group_with_name():
    azure_cdn_provisioning.validate_spec(
        VALID_COMMON, {**VALID_PROVIDER, "resource_group": "my-existing-rg", "use_existing_resource_group": True}
    )


def test_validate_spec_rejects_use_existing_resource_group_without_name():
    with pytest.raises(ApiError) as exc_info:
        azure_cdn_provisioning.validate_spec(VALID_COMMON, {**VALID_PROVIDER, "use_existing_resource_group": True})
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_passes_use_existing_resource_group_through():
    common, provider = azure_cdn_provisioning._derive(
        VALID_COMMON, {**VALID_PROVIDER, "resource_group": "my-existing-rg", "use_existing_resource_group": True}
    )
    tfvars = azure_cdn_provisioning.build_tfvars(1, "user-1-job-1", common, provider)
    assert tfvars["resource_group_name"] == "my-existing-rg"
    assert tfvars["use_existing_resource_group"] is True


def test_build_tfvars_https_redirect_forces_both_protocols():
    # https_redirect=true(기본값)면 protocol="https_only"를 선택해도 azurerm 요구사항 때문에
    # supported_protocols는 Http/Https 둘 다 있어야 한다 — 대신 forwarding_protocol에만
    # 사용자의 원래 선택(HttpsOnly)이 반영된다.
    common, provider = azure_cdn_provisioning._derive(
        VALID_COMMON, {**VALID_PROVIDER, "protocol": "https_only", "https_redirect": True}
    )
    tfvars = azure_cdn_provisioning.build_tfvars(1, "ws", common, provider)
    assert sorted(tfvars["supported_protocols"]) == ["Http", "Https"]
    assert tfvars["forwarding_protocol"] == "HttpsOnly"


def test_build_tfvars_no_redirect_respects_https_only_choice():
    common, provider = azure_cdn_provisioning._derive(
        VALID_COMMON, {**VALID_PROVIDER, "protocol": "https_only", "https_redirect": False}
    )
    tfvars = azure_cdn_provisioning.build_tfvars(1, "ws", common, provider)
    assert tfvars["supported_protocols"] == ["Https"]
    assert tfvars["forwarding_protocol"] == "HttpsOnly"


def test_build_tfvars_http_and_https_choice_uses_match_request():
    common, provider = azure_cdn_provisioning._derive(
        VALID_COMMON, {**VALID_PROVIDER, "protocol": "http_and_https", "https_redirect": False}
    )
    tfvars = azure_cdn_provisioning.build_tfvars(1, "ws", common, provider)
    assert sorted(tfvars["supported_protocols"]) == ["Http", "Https"]
    assert tfvars["forwarding_protocol"] == "MatchRequest"


def test_build_tfvars_clamps_health_probe_interval():
    common, provider = azure_cdn_provisioning._derive(
        VALID_COMMON, {**VALID_PROVIDER, "health_probe_interval_seconds": 9999}
    )
    tfvars = azure_cdn_provisioning.build_tfvars(1, "ws", common, provider)
    assert tfvars["health_probe_interval_seconds"] == 255


def test_run_calls_run_apply_with_credential_env(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, secrets=None, cancel_check=None):
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        captured["secrets"] = secrets
        return TerraformResult(
            success=True,
            outputs={
                "endpoint_hostname": "mcp-cdn-01-ep-abcd1234.z01.azurefd.net",
                "resource_group_name": "rg-cdn-ws-1",
                "route_name": "mcp-cdn-01-route",
            },
        )

    monkeypatch.setattr(azure_cdn_provisioning, "run_apply", fake_run_apply)

    result = azure_cdn_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        workspace_name="ws-1",
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload=VALID_SECRET,
        project_id=VALID_PROJECT_ID,
    )

    assert result.success is True
    assert captured["credential_env"] == {
        "ARM_TENANT_ID": "tenant-1",
        "ARM_CLIENT_ID": "client-1",
        "ARM_CLIENT_SECRET": "shh",
        "ARM_SUBSCRIPTION_ID": "sub-1",
    }
    assert captured["secrets"] == ["shh", "tenant-1", "client-1"]
    assert captured["tfvars"]["resource_group_name"] == "rg-cdn-ws-1"


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = azure_cdn_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec={"name": "Bad_Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload=VALID_SECRET,
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"


def test_run_returns_failed_result_on_missing_credential_fields(tmp_path):
    result = azure_cdn_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"tenant_id": "only-this"},
        project_id=VALID_PROJECT_ID,
    )
    assert result.success is False
    assert result.error_code == "CREDENTIAL_VERIFICATION_FAILED"


def test_run_returns_failed_result_when_project_id_missing_even_with_full_secret(tmp_path):
    # 회귀 테스트(2026-09-15) — secret_payload는 완전해도 project_id(구독 ID)가 안 넘어오면
    # 실패해야 한다. 마이페이지 UI 경로에서 실제로 겪었던 버그(구독 ID가 secret_payload가 아니라
    # cloud_accounts.external_account_id에만 저장됨)를 고정한다.
    result = azure_cdn_provisioning.run(
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
