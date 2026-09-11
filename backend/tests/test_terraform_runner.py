"""`app/terraform_runner.py` 단위 검증 — subprocess는 전부 monkeypatch로 대체한다(실제 terraform
바이너리나 네트워크를 쓰지 않는다)."""

from __future__ import annotations

import json
import subprocess

import pytest

from app import terraform_runner as tr


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setenv("TERRAFORM_PLUGIN_CACHE_DIR", str(tmp_path / "plugin-cache"))
    monkeypatch.setenv("TERRAFORM_APPLY_TIMEOUT_SECONDS", "5")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_prepare_workspace_copies_tf_files(tmp_path):
    module_dir = tmp_path / "module"
    module_dir.mkdir()
    (module_dir / "main.tf").write_text("# main")
    (module_dir / "not-tf.txt").write_text("ignore me")
    workspace_dir = tmp_path / "workspace"

    tr.prepare_workspace(workspace_dir, module_dir)

    assert (workspace_dir / "main.tf").exists()
    assert not (workspace_dir / "not-tf.txt").exists()


def _module_dir(tmp_path):
    module_dir = tmp_path / "module"
    module_dir.mkdir()
    (module_dir / "main.tf").write_text("# main")
    return module_dir


def test_run_apply_success_parses_outputs(monkeypatch, tmp_path):
    module_dir = _module_dir(tmp_path)
    workspace_dir = tmp_path / "ws"

    def fake_run(cmd, *, cwd, env, timeout):
        if cmd[1] == "init":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1] == "plan":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1] == "apply":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1] == "output":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"instance_id": {"value": "i-123"}}), stderr=""
            )
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(tr, "_run", fake_run)

    result = tr.run_apply(workspace_dir, module_dir, {"region": "ap-northeast-2"}, {"AWS_ACCESS_KEY_ID": "x"})

    assert result.success is True
    assert result.outputs == {"instance_id": "i-123"}
    assert (workspace_dir / "terraform.tfvars.json").exists()


def test_run_apply_init_failure_classifies_auth_error(monkeypatch, tmp_path):
    module_dir = _module_dir(tmp_path)
    workspace_dir = tmp_path / "ws"

    def fake_run(cmd, *, cwd, env, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Error: InvalidClientTokenId: bad token")

    monkeypatch.setattr(tr, "_run", fake_run)

    result = tr.run_apply(workspace_dir, module_dir, {}, {})

    assert result.success is False
    assert result.error_code == "PROVIDER_AUTHENTICATION_FAILED"


def test_run_apply_permission_denied_classification(monkeypatch, tmp_path):
    module_dir = _module_dir(tmp_path)
    workspace_dir = tmp_path / "ws"

    def fake_run(cmd, *, cwd, env, timeout):
        if cmd[1] == "init":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Error: UnauthorizedOperation")

    monkeypatch.setattr(tr, "_run", fake_run)

    result = tr.run_apply(workspace_dir, module_dir, {}, {})

    assert result.success is False
    assert result.error_code == "CLOUD_PERMISSION_DENIED"


def test_run_apply_stops_when_cancel_requested_before_plan(monkeypatch, tmp_path):
    module_dir = _module_dir(tmp_path)
    workspace_dir = tmp_path / "ws"
    calls = []

    def fake_run(cmd, *, cwd, env, timeout):
        calls.append(cmd[1])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tr, "_run", fake_run)

    result = tr.run_apply(workspace_dir, module_dir, {}, {}, cancel_check=lambda: True)

    assert result.cancelled is True
    assert result.success is False
    assert calls == ["init"]  # plan/apply never called


def test_run_apply_timeout_returns_failure(monkeypatch, tmp_path):
    module_dir = _module_dir(tmp_path)
    workspace_dir = tmp_path / "ws"

    def fake_run(cmd, *, cwd, env, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(tr, "_run", fake_run)

    result = tr.run_apply(workspace_dir, module_dir, {}, {})

    assert result.success is False
    assert result.error_code == "TERRAFORM_ERROR"


def test_run_apply_output_failure_still_reports_success(monkeypatch, tmp_path):
    """apply가 성공했으면 output 조회만 실패해도 실패로 되돌리지 않는다(중복 생성 방지 우선)."""
    module_dir = _module_dir(tmp_path)
    workspace_dir = tmp_path / "ws"

    def fake_run(cmd, *, cwd, env, timeout):
        if cmd[1] == "output":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tr, "_run", fake_run)

    result = tr.run_apply(workspace_dir, module_dir, {}, {})

    assert result.success is True
    assert result.outputs == {}


def test_run_destroy_success(monkeypatch, tmp_path):
    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    calls = []

    def fake_run(cmd, *, cwd, env, timeout):
        calls.append(cmd[1])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tr, "_run", fake_run)

    result = tr.run_destroy(workspace_dir, {"AWS_ACCESS_KEY_ID": "x"})

    assert result.success is True
    assert calls == ["destroy"]


def test_run_destroy_failure_classifies_error(monkeypatch, tmp_path):
    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()

    def fake_run(cmd, *, cwd, env, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Error: UnauthorizedOperation")

    monkeypatch.setattr(tr, "_run", fake_run)

    result = tr.run_destroy(workspace_dir, {})

    assert result.success is False
    assert result.error_code == "CLOUD_PERMISSION_DENIED"
from app.services import terraform_runner


def test_redact_removes_all_secret_occurrences():
    text = "error: authenticating client_id=abcd1234 client_secret=topsecret1 failed"

    redacted = terraform_runner.redact(text, ["topsecret1", "abcd1234"])

    assert "topsecret1" not in redacted
    assert "abcd1234" not in redacted
    assert "***REDACTED***" in redacted


def test_redact_ignores_empty_secret_values():
    text = "no secrets in this message"

    assert terraform_runner.redact(text, [""]) == text


def test_redact_ignores_too_short_values_to_avoid_mangling_message():
    # 실제로 발견된 문제: tenant_id="t" 같은 한 글자 값을 redact 대상에 넣으면 "Trace ID" 같은
    # 흔한 단어의 "T"까지 지워져 메시지 전체가 알아볼 수 없게 뭉개진다.
    text = "AADSTS900023: Trace ID: abc123"

    redacted = terraform_runner.redact(text, ["t", "c"])

    assert redacted == text
