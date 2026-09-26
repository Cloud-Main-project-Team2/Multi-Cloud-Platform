"""실행 전 점검(scripts/verify_preflight_check.py)의 판정 규칙을 고정한다.

이 점검은 "출력 보고 사람이 판단"이 아니라 **불일치면 실패로 끝나야** 하는 물건이라, 정상 설정이
통과하는 것과 **설정 하나씩 어긋났을 때 각각 실패하는 것**을 함께 고정한다. 네트워크·도커 없이 돈다.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify_preflight_check.py"
_spec = importlib.util.spec_from_file_location("verify_preflight_check", _PATH)
pf = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(pf)

HOST_HASHES = {"azure_cost": "a" * 64, "gating": "b" * 64}
ACCOUNT_ID = 16


def _facts(mode: str) -> dict:
    allowed = mode == "allow"
    return {
        "settings": {
            "db_name": "mcp_db_verify",
            "manual_providers": ["azure"] if allowed else [],
            "auto_providers": [],
            "account_ids_raw": f"azure:{ACCOUNT_ID}" if allowed else "",
            "max_pages": 2,
            "max_requests": 3,
            "cost_scheduler": False,
            "report_scheduler": False,
            "mail_host": "mailhog",
            "mail_port": 1025,
            "mail_username": "",
            "mail_use_tls": False,
        },
        "hashes": dict(HOST_HASHES),
        "gating": {
            "azure_manual_target": None if allowed else "ACCOUNT_NOT_ENABLED",
            "azure_manual_other": "ACCOUNT_NOT_ENABLED",
            "aws_manual_target": "INGEST_DISABLED",
            "azure_auto_target": False,
        },
        "probes": {"request_budget": True, "redirect_not_followed": True, "next_page_401": True},
    }


def _run(facts, mode="allow", account_id=ACCOUNT_ID):
    results = pf.evaluate(facts, mode=mode, account_id=account_id if mode == "allow" else None,
                          host_hashes=HOST_HASHES)
    return [name for name, ok, _ in results if not ok]


# --- 정상 설정은 통과한다 -------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["blocked", "allow"])
def test_correct_setup_passes(mode):
    assert _run(_facts(mode), mode=mode) == []


# --- 설정이 하나씩 어긋나면 각각 실패한다 ------------------------------------------------------


@pytest.mark.parametrize("path,value,expected_failure", [
    (("settings", "db_name"), "mcp_db", "db_target"),                  # 원본 DB를 보고 있다
    (("settings", "db_name"), "mcp_db_test", "db_target"),
    (("settings", "manual_providers"), ["aws", "azure"], "manual_gate"),
    (("settings", "auto_providers"), ["aws"], "auto_gate"),            # 자동 수집이 열려 있다
    (("settings", "account_ids_raw"), "azure:17", "account_allowlist"),
    (("settings", "account_ids_raw"), "azure:16,azure:17", "account_allowlist"),   # 두 계정 허용
    (("settings", "max_pages"), 50, "azure_max_pages"),
    (("settings", "max_requests"), 60, "azure_max_requests"),
    (("settings", "cost_scheduler"), True, "cost_scheduler_off"),
    (("settings", "report_scheduler"), True, "report_scheduler_off"),
    (("settings", "mail_host"), "smtp.example.com", "mailhog"),        # 실제 SMTP로 나간다
    (("settings", "mail_use_tls"), True, "mailhog"),
    (("settings", "mail_username"), "someone", "mailhog"),
    (("hashes", "azure_cost"), "c" * 64, "code_hash:azure_cost"),      # 컨테이너 코드가 다르다
    (("hashes", "gating"), "c" * 64, "code_hash:gating"),
    (("gating", "azure_manual_target"), "ACCOUNT_NOT_ENABLED", "gate_effect_target"),
    (("gating", "azure_manual_other"), None, "gate_effect_other"),     # 다른 계정까지 열렸다
    (("gating", "aws_manual_target"), None, "gate_effect_aws"),
    (("gating", "azure_auto_target"), True, "gate_effect_auto"),
    (("probes", "request_budget"), False, "behaviour:request_budget"),
    (("probes", "redirect_not_followed"), False, "behaviour:redirect_not_followed"),
    (("probes", "next_page_401"), False, "behaviour:next_page_401"),
])
def test_each_mismatch_fails(path, value, expected_failure):
    facts = copy.deepcopy(_facts("allow"))
    facts[path[0]][path[1]] = value

    failures = _run(facts)

    assert expected_failure in failures, failures


def test_blocked_mode_rejects_an_open_gate():
    """원복 확인용 blocked 모드는 '아무 계정도 허용 안 됨'을 요구한다."""
    facts = copy.deepcopy(_facts("blocked"))
    facts["gating"]["azure_manual_target"] = None       # 아직 열려 있다

    assert "gate_effect" in _run(facts, mode="blocked", account_id=None)


def test_missing_probe_result_is_a_failure_not_a_pass():
    """프로브가 아예 돌지 못한 경우(키 없음)를 통과로 읽지 않는다."""
    facts = copy.deepcopy(_facts("allow"))
    facts["probes"] = {}

    failures = _run(facts)

    assert {"behaviour:request_budget", "behaviour:redirect_not_followed",
            "behaviour:next_page_401"} <= set(failures)


# --- 계정 인수 ------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "abc", "0", "-3", "16.0", "16 17", " "])
def test_bad_account_argument_is_rejected(raw):
    with pytest.raises(ValueError):
        pf.parse_account_arg(raw)


def test_good_account_argument():
    assert pf.parse_account_arg(" 16 ") == 16


def test_allow_mode_without_account_id_fails():
    failures = pf.evaluate(_facts("allow"), mode="allow", account_id=None, host_hashes=HOST_HASHES)

    assert [n for n, ok, _ in failures if not ok] == ["account_arg"]


def test_unknown_mode_fails():
    failures = pf.evaluate(_facts("allow"), mode="whatever", account_id=None, host_hashes=HOST_HASHES)

    assert [n for n, ok, _ in failures if not ok] == ["mode"]


# --- 동작 확인 프로브 자체 ---------------------------------------------------------------------


def test_behaviour_probes_run_offline_and_pass():
    """프로브는 실제 SDK를 스텁 transport로 돌린다 — 네트워크 없이 3개 모두 통과해야 한다."""
    out = pf.run_behaviour_probes()

    assert out == {"request_budget": True, "redirect_not_followed": True, "next_page_401": True}
