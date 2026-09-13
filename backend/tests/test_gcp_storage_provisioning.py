"""`app/gcp_storage_provisioning.py` 단위 검증 — spec 허용 목록, tfvars 조립, run() 위임."""

from __future__ import annotations

import pytest

from app import gcp_storage_provisioning
from app.errors import ApiError
from app.terraform_runner import TerraformResult

VALID_COMMON = {"name": "my-unique-bucket-01"}
VALID_PROVIDER = {"region": "asia-northeast3", "storage_class": "Standard"}


def test_validate_spec_accepts_valid_input():
    gcp_storage_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


@pytest.mark.parametrize(
    "common,provider",
    [
        ({"name": "AB"}, VALID_PROVIDER),  # 대문자 불가
        ({"name": "ab"}, VALID_PROVIDER),  # 최소 3자
        ({"name": "-abc"}, VALID_PROVIDER),  # 하이픈으로 시작 불가
        ({"name": "google-bucket"}, VALID_PROVIDER),  # GCS 예약어(google 포함)
        ({"name": "goog-bucket-01"}, VALID_PROVIDER),  # GCS 예약어(goog 접두사)
        (VALID_COMMON, {**VALID_PROVIDER, "region": "eu-west-1"}),
        (VALID_COMMON, {**VALID_PROVIDER, "storage_class": "Glacier"}),  # GCP 허용 목록에 없음
    ],
)
def test_validate_spec_rejects_invalid_input(common, provider):
    with pytest.raises(ApiError) as exc_info:
        gcp_storage_provisioning.validate_spec(common, provider)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"


def test_build_tfvars_uses_bucket_name_without_mcp_prefix():
    tfvars = gcp_storage_provisioning.build_tfvars(42, "proj-1", "my-unique-bucket-01", "asia-northeast3", "STANDARD")
    assert tfvars["bucket_name"] == "my-unique-bucket-01"  # mcp- 접두사 없음(Compute/Cloud SQL과 다름)
    assert tfvars["storage_class"] == "STANDARD"
    assert tfvars["labels"]["job-id"] == "42"


@pytest.mark.parametrize(
    "label,api_value",
    [("Standard", "STANDARD"), ("Nearline", "NEARLINE"), ("Coldline", "COLDLINE"), ("Archive", "ARCHIVE")],
)
def test_storage_class_mapping(label, api_value):
    _, _, mapped = gcp_storage_provisioning._derive(VALID_COMMON, {**VALID_PROVIDER, "storage_class": label})
    assert mapped == api_value


def test_run_calls_run_apply_with_credentials_file(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, credentials_file=None, cancel_check=None):
        captured["tfvars"] = tfvars
        captured["credential_env"] = credential_env
        captured["credentials_file"] = credentials_file
        return TerraformResult(success=True, outputs={"bucket_name": "my-unique-bucket-01"})

    monkeypatch.setattr(gcp_storage_provisioning, "run_apply", fake_run_apply)

    result = gcp_storage_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec=VALID_COMMON,
        provider_spec=VALID_PROVIDER,
        secret_payload={"type": "service_account"},
    )

    assert result.success is True
    assert captured["tfvars"]["bucket_name"] == "my-unique-bucket-01"
    assert captured["credential_env"] == {}
    assert captured["credentials_file"] == {"type": "service_account"}


def test_run_returns_failed_result_on_invalid_spec(tmp_path):
    result = gcp_storage_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec={"name": "AB"},
        provider_spec=VALID_PROVIDER,
        secret_payload={"type": "service_account"},
    )
    assert result.success is False
    assert result.error_code == "VALIDATION_ERROR"
