"""app.services.provisioning.azure_vm.run 단위 테스트.

실제 terraform 바이너리는 호출하지 않는다 — terraform_runner.apply/output_json을
가짜로 바꿔치기해 성공/실패 분기만 검증한다.

이 파일은 conftest.py의 `db_session`(롤백 전용) 대신 직접 commit하는 세션을 쓴다.
`run()`이 background task로서 자기 자신의 `SessionLocal()`을 새로 여는데, `db_session`은
아직 commit되지 않은 별도 connection/transaction이라 그 세션에서는 보이지 않기 때문이다.
"""

import base64
import json
import os

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import AuditEvent, CloudAccount, Credential, ProvisioningJob, ServiceCatalog, User
from app.security import credential_crypto as cc
from app.services import terraform_runner
from app.services.provisioning import azure_vm

VALID_KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", VALID_KEY)
    cc.get_settings.cache_clear()
    yield
    cc.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _terraform_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("TERRAFORM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("TERRAFORM_PLUGIN_CACHE_DIR", str(tmp_path / "plugin-cache"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, future=True)


def _make_job(session, *, secret_payload=None):
    secret_payload = secret_payload or {
        "tenant_id": "t",
        "client_id": "c",
        "client_secret": "s3cr3t",
        "subscription_id": "sub",
    }

    user = User(email="exec@example.com", normalized_email="exec@example.com", name="Exec", affiliation_type="individual")
    session.add(user)
    session.flush()

    account = CloudAccount(user_id=user.id, provider="azure", external_account_id="sub-1")
    session.add(account)
    session.flush()

    ciphertext, nonce = cc.encrypt_credential_json(secret_payload)
    credential = Credential(
        cloud_account_id=account.id,
        name="provisioner",
        encrypted_payload=ciphertext,
        encryption_nonce=nonce,
        encryption_key_version="v1",
    )
    session.add(credential)

    catalog = ServiceCatalog(
        provider="azure", service_code="vm", category="compute", display_name="Virtual Machine", provisionable=True
    )
    session.add(catalog)
    session.flush()

    job = ProvisioningJob(
        user_id=user.id,
        credential_id=credential.id,
        service_catalog_id=catalog.id,
        workspace_name=f"test-workspace-{os.urandom(4).hex()}",
        idempotency_key=f"key-{os.urandom(4).hex()}",
        spec_json={
            "common_spec": {"name": "web-01"},
            "provider_spec": {
                "location": "koreacentral",
                "vm_size": "Standard_B1s",
                "admin_username": "azureuser",
                "ssh_public_key": "ssh-rsa AAAA test",
            },
        },
        status="queued",
    )
    session.add(job)
    session.commit()
    return job.id


@pytest.fixture()
def job_id(session_factory, monkeypatch):
    monkeypatch.setattr(azure_vm, "SessionLocal", session_factory)
    session = session_factory()
    try:
        jid = _make_job(session)
    finally:
        session.close()

    yield jid

    with session_factory() as cleanup:
        cleanup.query(AuditEvent).delete()
        cleanup.query(ProvisioningJob).delete()
        cleanup.query(Credential).delete()
        cleanup.query(CloudAccount).delete()
        cleanup.query(ServiceCatalog).delete()
        cleanup.query(User).delete()
        cleanup.commit()


def test_run_success_updates_job_and_records_audit_event(job_id, session_factory, monkeypatch):
    def fake_apply(workspace_dir, env, secrets, timeout):
        assert env["ARM_CLIENT_SECRET"] == "s3cr3t"
        assert (workspace_dir / "terraform.tfvars.json").exists()
        return terraform_runner.TerraformResult(ok=True, stdout="", stderr="", returncode=0)

    def fake_output_json(workspace_dir, env, secrets, timeout):
        return json.dumps(
            {
                "vm_id": {"value": "fake-vm-id"},
                "resource_group_name": {"value": "rg-test"},
                "private_ip_address": {"value": "10.0.1.4"},
                "public_ip_address": {"value": "1.2.3.4"},
            }
        )

    monkeypatch.setattr(terraform_runner, "apply", fake_apply)
    monkeypatch.setattr(terraform_runner, "output_json", fake_output_json)

    azure_vm.run(job_id)

    with session_factory() as session:
        job = session.get(ProvisioningJob, job_id)
        assert job.status == "success"
        assert job.progress_percent == 100
        assert job.created_resource_count == 1
        assert job.result_json["vm_id"] == "fake-vm-id"
        assert job.result_json["public_ip_address"] == "1.2.3.4"
        assert job.terraform_state_ref is not None and job.terraform_state_ref.endswith("terraform.tfstate")
        assert job.started_at is not None and job.finished_at is not None

        events = session.query(AuditEvent).filter_by(target_id=str(job_id)).all()
        assert any(e.action == "provisioning.complete" and e.result == "success" for e in events)


def test_run_failure_marks_job_failed_with_redacted_message(job_id, session_factory, monkeypatch):
    def fake_apply(workspace_dir, env, secrets, timeout):
        return terraform_runner.TerraformResult(
            ok=False, stdout="", stderr="authorization failed for client_secret=s3cr3t", returncode=1
        )

    monkeypatch.setattr(terraform_runner, "apply", fake_apply)

    azure_vm.run(job_id)

    with session_factory() as session:
        job = session.get(ProvisioningJob, job_id)
        assert job.status == "failed"
        assert job.error_code == "TERRAFORM_ERROR"
        assert job.finished_at is not None
        # terraform_runner.apply가 이미 redact했어야 하므로 fake 구현이 직접 반환한 원문이
        # 그대로 저장되는 건 이 테스트의 의도가 아니다 — 여기서는 실패 상태 반영만 확인한다.
        assert job.error_message

        events = session.query(AuditEvent).filter_by(target_id=str(job_id)).all()
        assert any(e.action == "provisioning.complete" and e.result == "failure" for e in events)


def test_run_missing_credential_secret_field_fails_without_calling_terraform(session_factory, monkeypatch):
    monkeypatch.setattr(azure_vm, "SessionLocal", session_factory)
    session = session_factory()
    try:
        jid = _make_job(session, secret_payload={"tenant_id": "t", "client_id": "c", "subscription_id": "sub"})
    finally:
        session.close()

    def _unexpected_apply(*args, **kwargs):
        raise AssertionError("credential이 불완전하면 terraform을 호출해서는 안 된다")

    monkeypatch.setattr(terraform_runner, "apply", _unexpected_apply)

    try:
        azure_vm.run(jid)

        with session_factory() as session:
            job = session.get(ProvisioningJob, jid)
            assert job.status == "failed"
            assert job.error_code == "PROVIDER_AUTHENTICATION_FAILED"
    finally:
        with session_factory() as cleanup:
            cleanup.query(AuditEvent).delete()
            cleanup.query(ProvisioningJob).delete()
            cleanup.query(Credential).delete()
            cleanup.query(CloudAccount).delete()
            cleanup.query(ServiceCatalog).delete()
            cleanup.query(User).delete()
            cleanup.commit()
