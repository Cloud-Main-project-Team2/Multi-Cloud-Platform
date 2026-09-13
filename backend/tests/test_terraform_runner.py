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


def test_run_apply_serializes_init_across_concurrent_calls(monkeypatch, tmp_path):
    # 실제로 재현된 버그: RDS job의 긴 apply(수 분) 도중 다른 job의 init이 겹치면 둘 다
    # 공유 TF_PLUGIN_CACHE_DIR에 동시에 provider를 설치하려다 "text file busy"로 실패했다.
    # init만 _INIT_LOCK으로 직렬화하는지 — 두 run_apply를 스레드로 동시에 돌려 init 실행 구간이
    # 절대 겹치지 않는지 확인한다(plan/apply/output은 겹쳐도 무방하므로 검사하지 않는다).
    import threading
    import time

    module_dir = _module_dir(tmp_path)
    active_inits = []
    max_concurrent = []
    lock = threading.Lock()

    def fake_run(cmd, *, cwd, env, timeout):
        if cmd[1] == "init":
            with lock:
                active_inits.append(1)
                max_concurrent.append(len(active_inits))
            time.sleep(0.05)  # 겹칠 기회를 준다 — 락이 없으면 max_concurrent에 2가 찍힌다
            with lock:
                active_inits.pop()
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1] == "output":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({}), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tr, "_run", fake_run)

    results = []

    def worker(n):
        ws = tmp_path / ("ws-" + str(n))
        results.append(tr.run_apply(ws, module_dir, {}, {}))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r.success for r in results)
    assert max(max_concurrent) == 1


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
# redact는 통합 후 flat terraform_runner(app/terraform_runner.py)로 옮겼다 — Azure 러너가 쓴다.


def test_redact_removes_all_secret_occurrences():
    text = "error: authenticating client_id=abcd1234 client_secret=topsecret1 failed"

    redacted = tr.redact(text, ["topsecret1", "abcd1234"])

    assert "topsecret1" not in redacted
    assert "abcd1234" not in redacted
    assert "***REDACTED***" in redacted


def test_redact_ignores_empty_secret_values():
    text = "no secrets in this message"

    assert tr.redact(text, [""]) == text


def test_redact_ignores_too_short_values_to_avoid_mangling_message():
    # 실제로 발견된 문제: tenant_id="t" 같은 한 글자 값을 redact 대상에 넣으면 "Trace ID" 같은
    # 흔한 단어의 "T"까지 지워져 메시지 전체가 알아볼 수 없게 뭉개진다.
    text = "AADSTS900023: Trace ID: abc123"

    redacted = tr.redact(text, ["t", "c"])

    assert redacted == text
