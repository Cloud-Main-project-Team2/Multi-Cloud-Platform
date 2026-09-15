"""`app/gcp_cdn_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, run() 위임.

백엔드 버킷은 `provider_spec.create_bucket`(bool, 생략 시 True)으로 고른다:
- True(기본): CDN 전용으로 서버가 `job_id` 기반 이름(`mcp-cdn-{job_id}`)으로 자동 생성.
  `backend_bucket_name`/`existing_bucket_public_ack`는 필요 없다.
- False: 사용자가 지정한 기존 GCS 버킷(`backend_bucket_name`)을 그대로 연결한다. 이 경로는 그
  버킷의 IAM을 자동으로 바꾸지 않으므로, 그 버킷을 GCP 콘솔에서 이미 공개 읽기로 설정해 뒀다는
  확인(`existing_bucket_public_ack=True`)이 필수다.
"""

from __future__ import annotations

import pytest

from app import gcp_cdn_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "cdn-01"}
VALID_PROVIDER = {"lb_stack_ack": True}
VALID_PROVIDER_EXISTING_BUCKET = {
    "lb_stack_ack": True,
    "create_bucket": False,
    "backend_bucket_name": "my-existing-bucket",
    "existing_bucket_public_ack": True,
}


def test_validate_spec_accepts_valid_input():
    gcp_cdn_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


def test_validate_spec_accepts_explicit_create_bucket_true():
    gcp_cdn_provisioning.validate_spec(VALID_COMMON, {**VALID_PROVIDER, "create_bucket": True})


def test_validate_spec_accepts_existing_bucket_choice():
    gcp_cdn_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER_EXISTING_BUCKET)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "Bad_Name"}, VALID_PROVIDER),
        ({"name": "a"}, VALID_PROVIDER),  # 정규식은 최소 2자
        (VALID_COMMON, {"lb_stack_ack": False}),
        (VALID_COMMON, {}),  # lb_stack_ack 누락
        (VALID_COMMON, {**VALID_PROVIDER, "create_bucket": "yes"}),  # bool 아님
        (VALID_COMMON, {**VALID_PROVIDER, "create_bucket": False}),  # backend_bucket_name 누락
        (
            VALID_COMMON,
            {**VALID_PROVIDER, "create_bucket": False, "backend_bucket_name": "Bad_Bucket_Name!"},
        ),  # GCS 버킷 이름 형식 위반
        (
            VALID_COMMON,
            {**VALID_PROVIDER, "create_bucket": False, "backend_bucket_name": "my-existing-bucket"},
        ),  # existing_bucket_public_ack 누락
        (
            VALID_COMMON,
            {
                **VALID_PROVIDER,
                "create_bucket": False,
                "backend_bucket_name": "my-existing-bucket",
                "existing_bucket_public_ack": False,
            },
        ),  # existing_bucket_public_ack가 false
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        gcp_cdn_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_derives_bucket_name_from_job_id_when_auto_create():
    tfvars = gcp_cdn_provisioning.build_tfvars(42, "proj-1", "mcp-cdn-01", True, None)
    assert tfvars["instance_name"] == "mcp-cdn-01"
    assert tfvars["bucket_name"] == "mcp-cdn-42"  # job_id 기반이라 항상 유일
    assert tfvars["project_id"] == "proj-1"
    assert tfvars["create_bucket"] is True


def test_build_tfvars_different_job_ids_never_collide():
    tfvars1 = gcp_cdn_provisioning.build_tfvars(1, "proj-1", "mcp-cdn-01", True, None)
    tfvars2 = gcp_cdn_provisioning.build_tfvars(2, "proj-1", "mcp-cdn-01", True, None)
    assert tfvars1["bucket_name"] != tfvars2["bucket_name"]


def test_build_tfvars_uses_given_name_when_using_existing_bucket():
    tfvars = gcp_cdn_provisioning.build_tfvars(42, "proj-1", "mcp-cdn-01", False, "my-existing-bucket")
    assert tfvars["bucket_name"] == "my-existing-bucket"
    assert tfvars["create_bucket"] is False


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
    assert captured["tfvars"]["create_bucket"] is True
    assert captured["credential_env"] == {}
    assert captured["credentials_file"] == {"type": "service_account"}


def test_run_uses_existing_bucket_name_when_create_bucket_false(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, credentials_file=None, cancel_check=None):
        captured["tfvars"] = tfvars
        return TerraformResult(
            success=True,
            outputs={
                "forwarding_rule_name": "mcp-cdn-01-fwd-rule",
                "ip_address": "34.1.2.3",
                "backend_bucket_name": "my-existing-bucket",
                "backend_bucket_created": False,
            },
        )

    monkeypatch.setattr(gcp_cdn_provisioning, "run_apply", fake_run_apply)

    result = gcp_cdn_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER_EXISTING_BUCKET,
        secret_payload={"type": "service_account"},
    )

    assert result.success is True
    assert captured["tfvars"]["bucket_name"] == "my-existing-bucket"
    assert captured["tfvars"]["create_bucket"] is False


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
