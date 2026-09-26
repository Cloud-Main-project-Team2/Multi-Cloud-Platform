"""제한 실수집 **실행 전 점검** 본문 — 컨테이너 안에서 돌고, 네트워크로 나가는 요청은 0건이다.

출력하지 않는 것: `DATABASE_URL` 전체·비밀값·전체 환경변수. DB는 **이름만**, 코드는 **해시만** 본다.

**하나라도 어긋나면 0이 아닌 코드로 끝난다.** 이 점검이 통과해야만 실수집으로 넘어간다 —
"출력을 눈으로 확인"에 기대지 않는다.

두 상태를 명시적으로 구분해 검사한다(어느 쪽인지 인수로 밝혀야 한다):

    blocked              수동·자동 모두 차단(허용 목록 비어 있음). 평소 상태이자 실수집 뒤 원복 상태.
    allow <account_id>   azure 수동만, **승인된 그 계정 하나만** 허용. 실수집 직전 상태.

기능 확인은 **문자열 검사로 대신하지 않는다** — 실제 SDK를 스텁 transport로 돌려(네트워크 없음)
요청 상한·리다이렉트 차단·후속 페이지 401 분류가 실제로 그렇게 동작하는지 본다.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys

EXPECTED_DB_DEFAULT = "mcp_db_verify"
EXPECTED_MAX_PAGES = 2
EXPECTED_MAX_REQUESTS = 3
MODES = ("blocked", "allow")


# ── 사실 수집 ────────────────────────────────────────────────────────────────────────────

def collect_settings_facts() -> dict:
    from app.config import get_settings

    s = get_settings()
    # URL 전체는 절대 찍지 않는다 — 마지막 경로 조각(=DB 이름)만 떼어 본다.
    db_name = s.database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    env_true = lambda k: os.environ.get(k, "false").lower() == "true"
    return {
        "db_name": db_name,
        "manual_providers": sorted(p for p in s.cost_ingest_providers.replace(",", " ").split() if p),
        "auto_providers": sorted(p for p in s.cost_auto_ingest_providers.replace(",", " ").split() if p),
        "account_ids_raw": s.cost_ingest_account_ids.strip(),
        "max_pages": int(s.cost_azure_max_pages),
        "max_requests": int(s.cost_azure_max_requests),
        "cost_scheduler": env_true("COST_SCHEDULER_ENABLED"),
        "report_scheduler": env_true("REPORT_SCHEDULER_ENABLED"),
        "mail_host": s.mail_host,
        "mail_port": int(s.mail_port),
        "mail_username": s.mail_username,
        "mail_use_tls": bool(s.mail_use_tls),
    }


def collect_code_hashes() -> dict:
    """**실제로 로드된** 모듈 파일의 해시(이미지 안에 있는 다른 사본이 아니라)."""
    from app.cost import azure_cost, gating

    out = {}
    for key, mod in (("azure_cost", azure_cost), ("gating", gating)):
        path = inspect.getsourcefile(mod)
        with open(path, "rb") as fh:
            out[key] = hashlib.sha256(fh.read()).hexdigest()
    return out


def collect_gating_facts(account_id: int | None) -> dict:
    from app.cost import gating

    probe_id = account_id if account_id is not None else 1
    other_id = probe_id + 1
    return {
        "azure_manual_target": gating.manual_ingest_denial("azure", probe_id),
        "azure_manual_other": gating.manual_ingest_denial("azure", other_id),
        "aws_manual_target": gating.manual_ingest_denial("aws", probe_id),
        "azure_auto_target": gating.auto_ingest_allowed("azure", probe_id),
    }


# ── 동작 확인(네트워크 없음) ───────────────────────────────────────────────────────────────

def run_behaviour_probes() -> dict:
    """설치된 실제 SDK를 스텁 transport로 돌려 **동작**을 본다. 소스 문자열 검사가 아니다.

    - 요청 상한: 남은 페이지가 있어도 승인 상한을 넘겨 보내지 않는다
    - 리다이렉트: 307을 따라가지 않고(추가 전송 0) 오류로 끝낸다
    - 후속 페이지 401: 첫 페이지와 같은 `PROVIDER_AUTHENTICATION_FAILED`로 분류한다
    """
    import datetime as dt

    import requests
    from azure.core.credentials import AccessToken
    from azure.core.pipeline.transport import HttpTransport, RequestsTransportResponse
    from azure.core.rest import HttpRequest as RestHttpRequest
    from azure.core.rest._requests_basic import RestRequestsTransportResponse

    import app.cost.azure_cost as az
    from app.config import get_settings

    columns = ["PreTaxCost", "UsageDate", "Currency", "ServiceName", "ChargeType"]

    def body(rows, next_link=None):
        return {"properties": {"columns": [{"name": c, "type": "String"} for c in columns],
                               "rows": rows, "nextLink": next_link}}

    def response(request, status, payload, headers):
        raw = requests.Response()
        raw.status_code = status
        raw._content = json.dumps(payload).encode() if payload is not None else b""
        raw._content_consumed = True
        raw.encoding = "utf-8"
        raw.reason = "OK" if status < 400 else "Error"
        raw.headers.update({"Content-Type": "application/json"})
        raw.headers.update(headers or {})
        if isinstance(request, RestHttpRequest):
            return RestRequestsTransportResponse(request=request, internal_response=raw)
        return RequestsTransportResponse(request, raw)

    class _Recorder(HttpTransport):
        def __init__(self, scripted):
            self.scripted = list(scripted)
            self.sent = []

        def send(self, request, **kwargs):
            self.sent.append(request)
            if not self.scripted:
                raise AssertionError("스텁에 없는 추가 요청이 나갔다")
            status, payload, headers = self.scripted.pop(0)
            return response(request, status, payload, headers)

        def open(self): pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

    class _Credential:
        def get_token(self, *scopes, **kwargs):
            return AccessToken("stub", int(dt.datetime.now(dt.timezone.utc).timestamp()) + 3600)

        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

    saved_env = {k: os.environ.get(k) for k in ("COST_AZURE_MAX_PAGES", "COST_AZURE_MAX_REQUESTS")}
    saved_transport, saved_credential = az.RequestsTransport, az.ClientSecretCredential
    secret = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}
    start, end = dt.date(2026, 9, 1), dt.date(2026, 9, 4)
    link = "https://management.azure.com/subscriptions/x/providers/Microsoft.CostManagement/query?api-version=2022-10-01&$skiptoken=A"
    out: dict[str, bool] = {}

    def fetch(scripted, *, pages=None, requests_cap=None):
        recorder = _Recorder(scripted)
        if pages is not None:
            os.environ["COST_AZURE_MAX_PAGES"] = str(pages)
        if requests_cap is not None:
            os.environ["COST_AZURE_MAX_REQUESTS"] = str(requests_cap)
        get_settings.cache_clear()
        az.RequestsTransport = lambda *a, **k: recorder
        az.ClientSecretCredential = lambda **kw: _Credential()
        result = az.AzureCostProvider(sleeper=lambda s: None).fetch(secret, "sub-probe", start, end)
        return result, recorder

    try:
        page = (200, body([[1.0, 20260901, "USD", "VM", "Usage"]], next_link=link), {})
        # 1) 요청 상한: 2페이지·2요청으로 묶으면 3번째 전송은 나가지 않는다
        r, rec = fetch([page, page, page], pages=2, requests_cap=2)
        out["request_budget"] = (r.partial is True and len(rec.sent) == 2 and r.api_calls == 2)

        # 2) 리다이렉트(307)를 따라가지 않는다 — 추가 전송 0, 빈 응답 아님
        r, rec = fetch([(307, None, {"Location": "https://elsewhere.example/q"})], pages=2, requests_cap=3)
        out["redirect_not_followed"] = (r.partial is True and r.rows == [] and len(rec.sent) == 1)

        # 3) 후속 페이지 401도 인증 실패로 분류한다
        r, rec = fetch([page, (401, {"error": {}}, {})], pages=2, requests_cap=3)
        out["next_page_401"] = (r.error_code == "PROVIDER_AUTHENTICATION_FAILED" and len(rec.sent) == 2)
    except Exception as exc:                          # noqa: BLE001 — 실패 사유를 점검 결과로 돌려준다
        out["probe_error"] = False
        out["probe_error_detail"] = str(exc)[:120]    # type: ignore[assignment]
    finally:
        az.RequestsTransport, az.ClientSecretCredential = saved_transport, saved_credential
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
    return out


# ── 판정 ────────────────────────────────────────────────────────────────────────────────

def parse_account_arg(raw: str | None) -> int:
    """`allow` 모드의 계정 인수. 숫자가 아니거나 0 이하면 **실패로 끝낸다**(임의 해석 없음)."""
    if raw is None or not str(raw).strip().isdigit():
        raise ValueError(f"계정 id가 올바르지 않습니다: {raw!r}")
    value = int(str(raw).strip())
    if value <= 0:
        raise ValueError(f"계정 id가 올바르지 않습니다: {raw!r}")
    return value


def evaluate(facts: dict, *, mode: str, account_id: int | None, host_hashes: dict,
             expected_db: str = EXPECTED_DB_DEFAULT) -> list[tuple[str, bool, str]]:
    """(이름, 통과, 설명) 목록. 순수 함수라 테스트가 조립한 facts로 그대로 돌릴 수 있다."""
    if mode not in MODES:
        return [("mode", False, f"알 수 없는 모드: {mode}")]
    if mode == "allow" and not isinstance(account_id, int):
        return [("account_arg", False, "allow 모드에는 계정 id가 필요합니다")]

    s, g, p = facts["settings"], facts["gating"], facts.get("probes", {})
    expected_accounts = "" if mode == "blocked" else f"azure:{account_id}"
    expected_manual = [] if mode == "blocked" else ["azure"]
    checks: list[tuple[str, bool, str]] = [
        ("db_target", s["db_name"] == expected_db, f"{s['db_name']} (기대 {expected_db})"),
        ("manual_gate", s["manual_providers"] == expected_manual,
         f"{s['manual_providers'] or '(비어 있음)'} (기대 {expected_manual or '(비어 있음)'})"),
        ("auto_gate", s["auto_providers"] == [], f"{s['auto_providers'] or '(비어 있음)'} (기대 (비어 있음))"),
        ("account_allowlist", s["account_ids_raw"] == expected_accounts,
         f"{s['account_ids_raw'] or '(비어 있음)'} (기대 {expected_accounts or '(비어 있음)'})"),
        ("azure_max_pages", s["max_pages"] == EXPECTED_MAX_PAGES, f"{s['max_pages']} (기대 {EXPECTED_MAX_PAGES})"),
        ("azure_max_requests", s["max_requests"] == EXPECTED_MAX_REQUESTS,
         f"{s['max_requests']} (기대 {EXPECTED_MAX_REQUESTS})"),
        ("cost_scheduler_off", s["cost_scheduler"] is False, f"{s['cost_scheduler']} (기대 False)"),
        ("report_scheduler_off", s["report_scheduler"] is False, f"{s['report_scheduler']} (기대 False)"),
        ("mailhog", (s["mail_host"] == "mailhog" and s["mail_port"] == 1025
                     and s["mail_username"] == "" and s["mail_use_tls"] is False),
         f"{s['mail_host']}:{s['mail_port']} tls={s['mail_use_tls']} user={'(없음)' if not s['mail_username'] else '(설정됨)'}"),
    ]

    for key, host_hash in sorted(host_hashes.items()):
        same = facts["hashes"].get(key) == host_hash
        checks.append((f"code_hash:{key}", same, f"container {str(facts['hashes'].get(key))[:16]} / host {host_hash[:16]}"))

    if mode == "blocked":
        checks.append(("gate_effect", g["azure_manual_target"] is not None and g["azure_manual_other"] is not None,
                       "azure 수동이 어느 계정에도 허용되지 않아야 한다"))
    else:
        checks.append(("gate_effect_target", g["azure_manual_target"] is None,
                       f"계정 {account_id} azure 수동이 허용돼야 한다 (현재 {g['azure_manual_target']})"))
        checks.append(("gate_effect_other", g["azure_manual_other"] is not None,
                       "승인 계정 외에는 차단돼야 한다"))
        checks.append(("gate_effect_aws", g["aws_manual_target"] is not None, "aws 수동은 차단돼야 한다"))
    checks.append(("gate_effect_auto", g["azure_auto_target"] is False, "자동 수집은 언제나 차단"))

    for name in ("request_budget", "redirect_not_followed", "next_page_401"):
        checks.append((f"behaviour:{name}", p.get(name) is True, "실제 SDK·스텁 transport 동작 확인"))
    return checks


def main(argv: list[str]) -> int:
    mode = (argv[0] if argv else "blocked").strip()
    try:
        account_id = parse_account_arg(argv[1] if len(argv) > 1 else None) if mode == "allow" else None
    except ValueError as exc:
        print(f"[FAIL] account_arg — {exc}")
        return 2
    host_hashes = json.loads(os.environ.get("PREFLIGHT_HOST_HASHES", "{}"))
    expected_db = os.environ.get("PREFLIGHT_EXPECTED_DB", EXPECTED_DB_DEFAULT)

    facts = {
        "settings": collect_settings_facts(),
        "hashes": collect_code_hashes(),
        "gating": collect_gating_facts(account_id),
        "probes": run_behaviour_probes(),
    }
    results = evaluate(facts, mode=mode, account_id=account_id, host_hashes=host_hashes, expected_db=expected_db)

    print(f"모드: {mode}" + (f" · 승인 계정 {account_id}" if account_id else ""))
    failed = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:24} {detail}")
        failed += 0 if ok else 1
    if not host_hashes:
        print("[FAIL] code_hash               호스트 해시를 받지 못했습니다(PREFLIGHT_HOST_HASHES)")
        failed += 1
    print(("모두 통과" if not failed else f"실패 {failed}건 — 실수집으로 진행하지 않는다"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
