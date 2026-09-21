"""`app/gcp_provisioning.py`(Compute Engine) 단위 검증 — spec 허용 목록, tfvars 조립."""

from __future__ import annotations

import pytest

from app import gcp_provisioning
from app.errors import ApiError

VALID_COMMON = {"name": "web-01"}
VALID_PROVIDER = {"region": "asia-northeast3", "instance_type": "e2-micro"}


def test_validate_spec_accepts_valid_input():
    gcp_provisioning.validate_spec(VALID_COMMON, VALID_PROVIDER)


def test_validate_spec_accepts_existing_network_and_subnetwork():
    gcp_provisioning.validate_spec(
        VALID_COMMON, {**VALID_PROVIDER, "network": "my-existing-vpc", "subnetwork": "my-subnet"}
    )


def test_validate_spec_accepts_network_self_link():
    gcp_provisioning.validate_spec(
        VALID_COMMON,
        {**VALID_PROVIDER, "network": "https://www.googleapis.com/compute/v1/projects/p/global/networks/n"},
    )


@pytest.mark.parametrize(
    "field",
    ["network", "subnetwork"],
)
def test_validate_spec_rejects_invalid_network_name(field):
    with pytest.raises(ApiError) as exc_info:
        gcp_provisioning.validate_spec(VALID_COMMON, {**VALID_PROVIDER, field: "Not Valid!"})
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert exc_info.value.details[0]["field"] == f"provider_spec.{field}"


def test_build_tfvars_defaults_network_fields_to_none():
    tfvars = gcp_provisioning.build_tfvars(42, "proj-1", "mcp-web-01", "asia-northeast3", "e2-micro")
    assert tfvars["network"] is None
    assert tfvars["subnetwork"] is None


def test_build_tfvars_includes_existing_network_when_given():
    tfvars = gcp_provisioning.build_tfvars(
        42, "proj-1", "mcp-web-01", "asia-northeast3", "e2-micro",
        network="my-existing-vpc", subnetwork="my-subnet",
    )
    assert tfvars["network"] == "my-existing-vpc"
    assert tfvars["subnetwork"] == "my-subnet"


def test_validate_spec_accepts_curated_image_family():
    gcp_provisioning.validate_spec(VALID_COMMON, {**VALID_PROVIDER, "image": "Ubuntu 22.04"})


def test_validate_spec_rejects_unknown_image_family():
    with pytest.raises(ApiError) as exc_info:
        gcp_provisioning.validate_spec(VALID_COMMON, {**VALID_PROVIDER, "image": "Windows Server 2022"})
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert exc_info.value.details[0]["field"] == "provider_spec.image"


def test_build_tfvars_defaults_image_to_debian_12():
    tfvars = gcp_provisioning.build_tfvars(42, "proj-1", "mcp-web-01", "asia-northeast3", "e2-micro")
    assert tfvars["image"] == "debian-cloud/debian-12"


def test_run_resolves_ubuntu_image_family(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, credentials_file=None, cancel_check=None):
        captured["tfvars"] = tfvars
        from app.terraform_runner import TerraformResult
        return TerraformResult(success=True, outputs={"instance_name": "mcp-web-01"})

    monkeypatch.setattr(gcp_provisioning, "run_apply", fake_run_apply)

    gcp_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec=VALID_COMMON,
        provider_spec={**VALID_PROVIDER, "image": "Ubuntu 22.04"},
        secret_payload={"type": "service_account"},
    )

    assert captured["tfvars"]["image"] == "ubuntu-os-cloud/ubuntu-2204-lts"


def test_run_passes_network_fields_through_to_run_apply(monkeypatch, tmp_path):
    captured = {}

    def fake_run_apply(workspace_dir, module_dir, tfvars, credential_env, *, credentials_file=None, cancel_check=None):
        captured["tfvars"] = tfvars
        from app.terraform_runner import TerraformResult
        return TerraformResult(success=True, outputs={"instance_name": "mcp-web-01"})

    monkeypatch.setattr(gcp_provisioning, "run_apply", fake_run_apply)

    gcp_provisioning.run(
        job_id=1,
        workspace_dir=tmp_path,
        project_id="proj-1",
        common_spec=VALID_COMMON,
        provider_spec={**VALID_PROVIDER, "network": "my-existing-vpc"},
        secret_payload={"type": "service_account"},
    )

    assert captured["tfvars"]["network"] == "my-existing-vpc"
    assert captured["tfvars"]["subnetwork"] is None
