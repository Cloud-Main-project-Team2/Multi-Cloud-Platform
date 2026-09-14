"""`app/gcp_cdn_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, run() 위임.

버킷은 사용자 입력이 아니라 CDN 전용으로 서버가 `job_id` 기반 이름(`mcp-cdn-{job_id}`)으로
자동 생성한다 — 그래서 `provider_spec`엔 `lb_stack_ack`만 있으면 된다(`backend_bucket_name`
없음).
"""

from __future__ import annotations

import pytest

from app import gcp_cdn_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "cdn-01"}
VALID_PROVIDER = {"lb_stack_ack": True}


def test_validate_spec_accepts_valid_input():
    gcp_cdn_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 정규식은 최소 2자
        (VALID_COMMON, {"lb_stack_ack": False}),
        (VALID_COMMON, {}),  # lb_stack_ack 누락
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        gcp_cdn_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_derives_bucket_name_from_job_id():
    tfvars = gcp_cdn_provisioning.build_tfvars(42, "proj-1", "mcp-cdn-01")
    assert tfvars["instance_name"] == "mcp-cdn-01"
    assert tfvars["bucket_name"] == "mcp-cdn-42"  # job_id 기반이라 항상 유일
    assert tfvars["project_id"] == "proj-1"


def test_build_tfvars_different_job_ids_never_collide():
    tfvars1 = gcp_cdn_provisioning.build_tfvars(1, "proj-1", "mcp-cdn-01")
    tfvars2 = gcp_cdn_provisioning.build_tfvars(2, "proj-1", "mcp-cdn-01")
    assert tfvars1["bucket_name"] != tfvars2["bucket_name"]


def test_run_calls_run_apply_with_credentials_file(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, credentials_file=None, cancel_check=None):
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        captured["credentials_file"] = credentials_file
        return TerraformResult(
            success=True,
            outputs={"forwarding_rule_name": "mcp-cdn-01-fwd-rule", "ip_address": "34.1.2.3", "bucket_name": "mcp-cdn-1"},
        )

    monkeypatch.setattr(gcp_cdn_provisioning, "run_apply", fake_run_apply)

    result = gcp_cdn_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"type": "service_account"},
    )

    assert result.success is True
    assert captured["tfvars"]["instance_name"] == "mcp-cdn-01"
    assert captured["tfvars"]["bucket_name"] == "mcp-cdn-1"
    assert captured["credential_env"] == {}
    assert captured["credentials_file"] == {"type": "service_account"}


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = gcp_cdn_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec={"name": "Bad_Name"},
        provider_spec=VALID_PROVIDER,
        secret_payload={"type": "service_account"},
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"
