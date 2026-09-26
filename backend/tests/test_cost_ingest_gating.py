"""수집 활성화 제어(app/cost/gating.py) 검증 — 실제 CSP는 한 번도 호출하지 않는다.

새 CSP(azure)는 **모의 어댑터**를 `COST_ADAPTERS`에 잠시 끼워 "구현은 됐는데 아직 켜지 않은"
상태를 만든다. 어댑터의 `fetch()`가 불렸는지를 세어서 "호출 0회"를 숫자로 증명한다.

세 질문을 구분해서 본다: ① 구현 지원(UNSUPPORTED) ② 수동 허용 ③ 자동 허용.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import app.cost as cost_pkg
import app.cost.scheduler as scheduler_module
import app.routers.costs as costs_router
from app.config import get_settings
from app.cost import gating
from app.cost.base import CostFetchResult, CostRow
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Credential
from app.security.credential_crypto import encrypt_credential_json

NOW = dt.datetime.now(dt.timezone.utc)


class _CountingProvider:
    """모의 어댑터 — 실제 CSP 대신 호출 횟수만 센다."""

    calls = 0

    def fetch(self, secret_payload, external_account_id, period_start, period_end):
        type(self).calls += 1
        return CostFetchResult(
            rows=[CostRow(period_start=period_start, period_end=period_start + dt.timedelta(days=1),
                          service="Mock", charge_category="usage", amount=Decimal("1.000000"),
                          currency="USD", is_estimated=False, source_record_key="mock:1")],
            currency="USD", covered_through=period_end - dt.timedelta(days=1), api_calls=1,
        )


@pytest.fixture()
def mock_azure_adapter(monkeypatch):
    """azure를 "구현은 돼 있다" 상태로 만든다(활성화는 별개)."""
    _CountingProvider.calls = 0
    monkeypatch.setitem(cost_pkg.COST_ADAPTERS, "azure", _CountingProvider)
    yield _CountingProvider


def _set_gating(monkeypatch, *, manual="aws", auto="aws", account_ids=""):
    monkeypatch.setenv("COST_INGEST_PROVIDERS", manual)
    monkeypatch.setenv("COST_AUTO_INGEST_PROVIDERS", auto)
    monkeypatch.setenv("COST_INGEST_ACCOUNT_IDS", account_ids)
    get_settings.cache_clear()


def _account(db, user, provider="aws", ext="111122223333"):
    a = CloudAccount(user_id=user.id, provider=provider, external_account_id=ext, account_label=f"{provider}-acct")
    db.add(a)
    db.flush()
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    db.add(Credential(cloud_account_id=a.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
                      encryption_key_version="v1", verified=True, permission_scope={"cost_read": True}))
    db.flush()
    return a


def _post(client, user, auth_header, **body):
    return client.post("/api/v1/cost-ingestion-runs", json=body, headers=auth_header(user))


def _skipped(resp):
    return {s["cloud_account_id"]: s["reason_code"] for s in resp.json()["data"]["skipped"]}


# --- ① 구현 지원 vs ② 수동 허용은 다른 질문이다 ------------------------------------------------


def test_unsupported_provider_is_not_disguised_as_disabled(client, make_user, auth_header, db_session, monkeypatch):
    """어댑터 자체가 없으면 UNSUPPORTED다 — 활성화 설정과 무관하다.
    (2026-09-23: Azure 어댑터가 생겨서 "구현 없음" 예시를 아직 수집기가 없는 GCP로 바꿨다.)"""
    _set_gating(monkeypatch, manual="aws,gcp", account_ids="gcp:999999")
    user = make_user()
    acct = _account(db_session, user, provider="gcp", ext="proj-1")
    db_session.commit()

    resp = _post(client, user, auth_header, cloud_account_ids=[str(acct.id)])

    assert resp.status_code == 202
    assert _skipped(resp)[str(acct.id)] == "UNSUPPORTED"


def test_new_provider_registered_but_disabled_makes_zero_calls(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter
):
    """어댑터를 등록만 해도(구현 지원) 기본 설정에서는 수집이 꺼져 있다 — CSP 호출 0회, run 0건."""
    _set_gating(monkeypatch)  # 기본값 aws만
    user = make_user()
    acct = _account(db_session, user, provider="azure", ext="sub-1")
    db_session.commit()

    resp = _post(client, user, auth_header, cloud_account_ids=[str(acct.id)])

    assert resp.status_code == 202
    assert resp.json()["data"]["items"] == []
    assert _skipped(resp)[str(acct.id)] == "INGEST_DISABLED"
    assert mock_azure_adapter.calls == 0
    assert db_session.query(CostIngestionRun).count() == 0   # 거부된 요청은 run을 만들지 않는다


def test_provider_enabled_but_account_not_allowlisted(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter
):
    """CSP를 켜도 허용 목록에 없는 계정은 막힌다 — 빈 목록을 '전체 허용'으로 읽지 않는다."""
    _set_gating(monkeypatch, manual="aws,azure", account_ids="")   # azure 켬 + 계정 목록 비움
    user = make_user()
    acct = _account(db_session, user, provider="azure", ext="sub-1")
    db_session.commit()

    resp = _post(client, user, auth_header, cloud_account_ids=[str(acct.id)])

    assert _skipped(resp)[str(acct.id)] == "ACCOUNT_NOT_ENABLED"
    assert mock_azure_adapter.calls == 0
    assert db_session.query(CostIngestionRun).count() == 0


def test_allowlisted_account_can_run_manually_but_others_cannot(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter
):
    """허용한 계정만 수동 수집된다. 같은 CSP의 다른 계정은 그대로 막힌다."""
    user = make_user()
    allowed = _account(db_session, user, provider="azure", ext="sub-allowed")
    blocked = _account(db_session, user, provider="azure", ext="sub-blocked")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", account_ids=f"azure:{allowed.id}")

    resp = _post(client, user, auth_header, cloud_account_ids=[str(allowed.id), str(blocked.id)])

    assert [i["cloud_account_id"] for i in resp.json()["data"]["items"]] == [str(allowed.id)]
    assert _skipped(resp)[str(blocked.id)] == "ACCOUNT_NOT_ENABLED"


# --- 진입 경로가 달라도 같은 제어 ---------------------------------------------------------------


def test_same_control_when_account_ids_omitted(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter
):
    """계정 id를 생략하면 '내 계정 전부'가 대상이 된다 — 이 경로에도 같은 판정이 걸린다.
    AWS 계정은 기본 설정에서 그대로 통과한다(새 제어가 기존 CSP를 막지 않는다)."""
    _set_gating(monkeypatch)
    user = make_user()
    aws = _account(db_session, user, provider="aws", ext="111122223333")
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    db_session.commit()

    resp = _post(client, user, auth_header)

    assert [i["cloud_account_id"] for i in resp.json()["data"]["items"]] == [str(aws.id)]
    assert _skipped(resp)[str(azure.id)] == "INGEST_DISABLED"


def test_allowlist_does_not_exclude_existing_aws_accounts(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter
):
    """azure 계정만 적은 허용 목록이 AWS 계정을 의도치 않게 제외하지 않는다."""
    user = make_user()
    aws = _account(db_session, user, provider="aws", ext="111122223333")
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", account_ids=f"azure:{azure.id}")

    resp = _post(client, user, auth_header)

    assert {i["cloud_account_id"] for i in resp.json()["data"]["items"]} == {str(aws.id), str(azure.id)}
    assert _skipped(resp) == {}


def test_other_users_account_is_still_rejected_even_if_allowlisted(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter
):
    """허용 목록은 소유권 검사를 대체하지 않는다 — 남의 계정 id를 적어도 404다."""
    owner = make_user(email="owner@example.com")
    other = make_user(email="other@example.com")
    acct = _account(db_session, owner, provider="azure", ext="sub-1")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", account_ids=f"azure:{acct.id}")

    resp = _post(client, other, auth_header, cloud_account_ids=[str(acct.id)])

    assert resp.status_code == 404
    assert db_session.query(CostIngestionRun).count() == 0


# --- 잘못된 설정이 전체 허용으로 번지지 않는다 --------------------------------------------------


@pytest.mark.parametrize("manual,account_ids", [
    ("", ""),                       # 설정 누락
    ("azure-prod", "azure:1"),      # 알 수 없는 provider 이름
    ("aws,azure", "azure"),         # 형식 오류(콜론 없음)
    ("aws,azure", "azure:abc"),     # 숫자가 아닌 계정 id
    ("aws,azure", "azure:"),        # 값 없음
])
def test_malformed_settings_never_widen_access(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter, manual, account_ids
):
    _set_gating(monkeypatch, manual=manual, account_ids=account_ids)
    user = make_user()
    acct = _account(db_session, user, provider="azure", ext="sub-1")
    db_session.commit()

    resp = _post(client, user, auth_header, cloud_account_ids=[str(acct.id)])

    assert _skipped(resp)[str(acct.id)] in ("INGEST_DISABLED", "ACCOUNT_NOT_ENABLED")
    assert mock_azure_adapter.calls == 0


# --- ③ 자동 허용은 수동과 독립이다 --------------------------------------------------------------


def test_manual_enabled_does_not_enable_auto(db_session, make_user, monkeypatch, mock_azure_adapter):
    """수동만 허용한 계정은 자동 수집 대상이 아니다 — 스케줄러가 CSP를 부르지 않는다."""
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", auto="aws", account_ids=f"azure:{azure.id}")

    assert gating.manual_ingest_denial("azure", azure.id) is None
    assert gating.auto_ingest_allowed("azure", azure.id) is False

    called: list[int] = []
    monkeypatch.setattr(scheduler_module, "_run_single_account",
                        lambda db, account, *a, **k: called.append(account.id))
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(scheduler_module, "evaluate_and_notify_for_account_safely", lambda db, account: None)

    scheduler_module.run_daily_ingestion()

    assert called == []                       # azure는 자동 대상 아님
    assert mock_azure_adapter.calls == 0


def test_auto_enabled_account_runs_and_others_do_not(db_session, make_user, monkeypatch, mock_azure_adapter):
    """자동을 명시적으로 켠 계정만 스케줄러가 돈다. AWS 기존 동작은 그대로다."""
    user = make_user()
    aws = _account(db_session, user, provider="aws", ext="111122223333")
    allowed = _account(db_session, user, provider="azure", ext="sub-allowed")
    blocked = _account(db_session, user, provider="azure", ext="sub-blocked")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", auto="aws,azure", account_ids=f"azure:{allowed.id}")

    called: list[int] = []
    monkeypatch.setattr(scheduler_module, "_run_single_account",
                        lambda db, account, *a, **k: called.append(account.id))
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(scheduler_module, "evaluate_and_notify_for_account_safely", lambda db, account: None)

    scheduler_module.run_daily_ingestion()

    assert set(called) == {aws.id, allowed.id}
    assert blocked.id not in called


# --- 접수와 실행 사이에 설정이 바뀌는 경로 -------------------------------------------------------


def test_disabled_between_accept_and_execution_fails_without_csp_call(
    db_session, make_user, monkeypatch, mock_azure_adapter
):
    """접수 뒤 설정이 꺼지면 실행 시점에 막는다 — CSP를 부르지 않고 run을 실패로 종결한다."""
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    run = CostIngestionRun(
        user_id=user.id, cloud_account_id=azure.id, trigger_type="manual", status="pending",
        period_start=dt.date(2026, 9, 1), period_end=dt.date(2026, 9, 2), requested_at=NOW,
    )
    db_session.add(run)
    db_session.commit()

    _set_gating(monkeypatch)  # 실행 시점에는 꺼져 있다
    monkeypatch.setattr(costs_router, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    costs_router._run_cost_ingestion_run_inner(run.id)

    db_session.refresh(run)
    assert run.status == "failed"
    assert run.error_code == "INGEST_DISABLED"
    assert mock_azure_adapter.calls == 0


# --- 수집을 꺼도 이미 저장된 데이터는 그대로 보인다 ----------------------------------------------


def test_disabling_ingest_does_not_hide_stored_costs(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter
):
    """활성화 제어는 '새 CSP 호출'만 막는다 — 저장된 비용과 마지막 성공 정보는 조회에 그대로 남는다."""
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    day = dt.date(2026, 9, 1)
    db_session.add(CloudAccountCost(
        cloud_account_id=azure.id, provider="azure", charge_category="usage", service="Mock",
        amount=Decimal("12.500000"), currency="USD", period_start=day, period_end=day + dt.timedelta(days=1),
        as_of=NOW, source="azure_cost_management", source_record_key="azure:sub-1:2026-09-01:Mock:usage",
    ))
    db_session.add(CostIngestionRun(
        user_id=user.id, cloud_account_id=azure.id, trigger_type="manual", status="success",
        period_start=day, period_end=day + dt.timedelta(days=1), requested_at=NOW, started_at=NOW,
        finished_at=NOW, records_replaced=1,
    ))
    db_session.commit()
    _set_gating(monkeypatch)  # 수집 꺼짐

    resp = client.get("/api/v1/costs/summary",
                      params={"period_start": "2026-09-01", "period_end": "2026-09-02"},
                      headers=auth_header(user))

    assert resp.status_code == 200
    acc = [a for a in resp.json()["data"]["accounts"] if a["cloud_account_id"] == str(azure.id)][0]
    assert acc["actual"] == "12.500000"      # 금액이 숨겨지지 않는다
    assert acc["as_of"] is not None          # 마지막 성공 정보도 그대로


# --- 설정 문자열 해석 — compose 치환 결과와 앱 해석이 같아야 한다 --------------------------------
# scripts/check_cost_ingest_env.sh가 `docker compose config`로 치환 결과를 보여주고, 아래 표가
# **그 문자열을 앱이 어떻게 읽는지**를 고정한다. 둘을 따로 검증해야 compose 문법 오류
# (`${VAR:-aws}`가 빈 문자열까지 aws로 바꾸던 버그)를 다시 놓치지 않는다.


@pytest.mark.parametrize("raw,expected", [
    ("aws", {"aws"}),                       # ① 미설정일 때 compose가 넣어 주는 값
    ("", set()),                            # ② 명시적 빈 문자열 — 전부 꺼짐
    ("aws,azure", {"aws", "azure"}),        # ④
    ("azure-prod", set()),                  # ⑤ 잘못된 이름 → 무시. 기본값으로 되돌아가지 않는다
    ("aws, AZURE ,", {"aws", "azure"}),     # 공백·대문자·빈 항목 허용
])
def test_provider_list_parsing_matches_compose_values(monkeypatch, raw, expected):
    _set_gating(monkeypatch, manual=raw, auto=raw)
    assert set(gating.manual_ingest_providers()) == expected
    assert set(gating.auto_ingest_providers()) == expected


def test_invalid_provider_list_also_disables_aws(client, make_user, auth_header, db_session, monkeypatch):
    """provider 목록에 유효한 값이 하나도 없으면 **AWS도 꺼진다** — 설정 오류가 기본값으로
    조용히 복구되지 않는다(잘못된 설정이 전체 허용으로 번지지 않는다는 원칙의 반대편)."""
    _set_gating(monkeypatch, manual="azure-prod", auto="azure-prod")
    user = make_user()
    aws = _account(db_session, user, provider="aws", ext="111122223333")
    db_session.commit()

    resp = _post(client, user, auth_header, cloud_account_ids=[str(aws.id)])

    assert _skipped(resp)[str(aws.id)] == "INGEST_DISABLED"
    assert gating.auto_ingest_allowed("aws", aws.id) is False


# --- 허용된 계정은 실행·저장까지 간다(접수 목록에 들었다는 것만으로 성공이라 하지 않는다) --------


def test_allowed_account_runs_to_completion_and_stores_rows(
    client, make_user, auth_header, db_session, monkeypatch, mock_azure_adapter
):
    """허용된 계정은 접수 → 백그라운드 실행 → 행 저장 → run success까지 끝난다.

    TestClient의 BackgroundTasks가 별도 세션을 쓰므로, 실행 함수만 같은 테스트 세션으로 직접
    호출해 저장 결과를 본다(모의 어댑터라 CSP 호출은 없다)."""
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", account_ids=f"azure:{azure.id}")

    resp = _post(client, user, auth_header, cloud_account_ids=[str(azure.id)],
                 period_start="2026-09-01", period_end="2026-09-02")
    assert resp.status_code == 202
    run_id = int(resp.json()["data"]["items"][0]["id"])

    monkeypatch.setattr(costs_router, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    costs_router._run_cost_ingestion_run_inner(run_id)

    run = db_session.get(CostIngestionRun, run_id)
    assert run.status == "success"
    assert run.records_replaced == 1
    assert mock_azure_adapter.calls == 1        # 허용된 계정에서만 어댑터가 실제로 불렸다
    rows = db_session.query(CloudAccountCost).filter(CloudAccountCost.cloud_account_id == azure.id).all()
    assert [(r.service, str(r.amount), r.currency) for r in rows] == [("Mock", "1.000000", "USD")]


# --- Azure 어댑터 등록 후에도 기본값에서는 호출 0회 (B단계) ---------------------------------------


def test_registered_azure_adapter_is_not_called_by_default(client, make_user, auth_header, db_session, monkeypatch):
    """실제 어댑터가 COST_ADAPTERS에 등록돼 있어도 기본 설정에서는 수동 수집이 막힌다 —
    fetch()가 불리면 실패하도록 감시한다(실제 Azure 호출은 물론 없다)."""
    import app.cost.azure_cost as az

    called = []
    monkeypatch.setattr(az.AzureCostProvider, "fetch",
                        lambda self, *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError("호출되면 안 된다")))
    _set_gating(monkeypatch)   # 기본값: aws만
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-real")
    db_session.commit()

    resp = _post(client, user, auth_header, cloud_account_ids=[str(azure.id)])

    assert _skipped(resp)[str(azure.id)] == "INGEST_DISABLED"
    assert called == []
    assert db_session.query(CostIngestionRun).count() == 0


def test_manual_and_auto_paths_use_the_same_source(monkeypatch):
    """수동·자동이 다른 source로 저장하면 범위 교체가 서로를 덮지 못해 금액이 두 배가 된다."""
    from app.cost import cost_source_for

    assert cost_source_for("aws") == "aws_cost_explorer"        # 기존 값 유지(AWS 회귀)
    assert cost_source_for("azure") == "azure_cost_management"
    assert cost_source_for("gcp") == "gcp_bigquery_billing"

    import inspect
    import app.cost.scheduler as sched
    import app.routers.costs as costs_router
    for mod in (sched, costs_router):
        src = inspect.getsource(mod)
        assert "source=cost_source_for(account.provider)" in src
        assert 'f"{account.provider}_cost_explorer"' not in src


def test_allowed_azure_account_stores_rows_with_azure_source(
    client, make_user, auth_header, db_session, monkeypatch
):
    """허용한 Azure 계정은 수집·저장까지 가고, 저장 출처가 azure_cost_management다(모의 응답)."""
    import app.cost.azure_cost as az
    from app.cost.base import CostFetchResult, CostRow

    day = dt.date(2026, 9, 1)
    monkeypatch.setattr(az.AzureCostProvider, "fetch", lambda self, *a, **k: CostFetchResult(
        rows=[CostRow(period_start=day, period_end=day + dt.timedelta(days=1), service="Virtual Machines",
                      charge_category="usage", amount=Decimal("1500.000000"), currency="KRW",
                      is_estimated=True, source_record_key="azure:sub:2026-09-01:VM:Usage")],
        currency="KRW", covered_through=None, api_calls=1, partial=False, coverage_basis="observed_only"))

    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-allowed-real")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", account_ids=f"azure:{azure.id}")

    resp = _post(client, user, auth_header, cloud_account_ids=[str(azure.id)],
                 period_start="2026-09-01", period_end="2026-09-02")
    run_id = int(resp.json()["data"]["items"][0]["id"])
    monkeypatch.setattr(costs_router, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    costs_router._run_cost_ingestion_run_inner(run_id)

    run = db_session.get(CostIngestionRun, run_id)
    assert run.status == "success" and run.coverage_basis == "observed_only"   # 승격되지 않는다
    rows = db_session.query(CloudAccountCost).filter(CloudAccountCost.cloud_account_id == azure.id).all()
    assert [(r.source, str(r.amount), r.currency) for r in rows] == [("azure_cost_management", "1500.000000", "KRW")]


def test_recollection_replaces_rows_without_duplicates(client, make_user, auth_header, db_session, monkeypatch):
    """같은 범위를 다시 수집하면 범위 교체로 덮어써야 한다 — 중복 행이 쌓이면 금액이 두 배가 된다."""
    import app.cost.azure_cost as az
    from app.cost.base import CostFetchResult, CostRow

    day = dt.date(2026, 9, 1)
    def _fetch(amount):
        return lambda self, *a, **k: CostFetchResult(
            rows=[CostRow(period_start=day, period_end=day + dt.timedelta(days=1), service="VM",
                          charge_category="usage", amount=Decimal(amount), currency="KRW",
                          is_estimated=True, source_record_key="azure:sub:2026-09-01:VM:Usage")],
            currency="KRW", covered_through=None, api_calls=1, partial=False, coverage_basis="observed_only")

    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-recollect")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", account_ids=f"azure:{azure.id}")
    monkeypatch.setattr(costs_router, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    for amount in ("1000.000000", "2000.000000"):
        monkeypatch.setattr(az.AzureCostProvider, "fetch", _fetch(amount))
        # 수동 1시간 제한은 이 테스트의 관심사가 아니다 — 이전 run을 자동으로 돌려 제한 기준에서 뺀다.
        db_session.query(CostIngestionRun).update({CostIngestionRun.trigger_type: "auto"})
        db_session.commit()
        resp = _post(client, user, auth_header, cloud_account_ids=[str(azure.id)],
                     period_start="2026-09-01", period_end="2026-09-02")
        assert resp.status_code == 202, resp.text
        items = resp.json()["data"]["items"]
        assert items, resp.text
        costs_router._run_cost_ingestion_run_inner(int(items[0]["id"]))

    rows = db_session.query(CloudAccountCost).filter(CloudAccountCost.cloud_account_id == azure.id).all()
    assert [str(r.amount) for r in rows] == ["2000.000000"]     # 한 행만 남고 최신 값이다


def test_bad_second_page_preserves_existing_rows_and_basis(client, make_user, auth_header, db_session, monkeypatch):
    """2페이지가 잘못돼 partial로 끝나면 **저장 경로까지 가서** 기존 행과 성공 근거가 그대로여야 한다.
    (어댑터 단위가 아니라 run 실행 경로 전체를 본다.)"""
    import app.cost.azure_cost as az
    from app.cost.base import CostFetchResult, CostRow
    from app.cost.coverage import BASIS_OBSERVED_ONLY

    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-partial")
    day = dt.date(2026, 9, 1)
    db_session.add(CloudAccountCost(
        cloud_account_id=azure.id, provider="azure", charge_category="usage", service="VM",
        amount=Decimal("777.000000"), currency="KRW", period_start=day, period_end=day + dt.timedelta(days=1),
        as_of=NOW, source="azure_cost_management", source_record_key="azure:sub:2026-09-01:VM:Usage"))
    good_run = CostIngestionRun(
        user_id=user.id, cloud_account_id=azure.id, trigger_type="auto", status="success",
        period_start=day, period_end=day + dt.timedelta(days=1), requested_at=NOW, started_at=NOW,
        finished_at=NOW, records_replaced=1, coverage_basis=BASIS_OBSERVED_ONLY)
    db_session.add(good_run)
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", account_ids=f"azure:{azure.id}")

    # 1페이지는 받았지만 2페이지가 형식 오류 → partial(행은 일부만 담겨 온다)
    monkeypatch.setattr(az.AzureCostProvider, "fetch", lambda self, *a, **k: CostFetchResult(
        rows=[CostRow(period_start=day, period_end=day + dt.timedelta(days=1), service="VM",
                      charge_category="usage", amount=Decimal("1.000000"), currency="KRW",
                      is_estimated=True, source_record_key="azure:sub:2026-09-01:VM:Usage")],
        currency="KRW", covered_through=None, api_calls=2, partial=True,
        error_code="PROVIDER_API_ERROR", coverage_basis="observed_only"))

    resp = _post(client, user, auth_header, cloud_account_ids=[str(azure.id)],
                 period_start="2026-09-01", period_end="2026-09-02")
    run_id = int(resp.json()["data"]["items"][0]["id"])
    monkeypatch.setattr(costs_router, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    costs_router._run_cost_ingestion_run_inner(run_id)

    run = db_session.get(CostIngestionRun, run_id)
    assert run.status == "partial_success" and run.error_code == "PROVIDER_API_ERROR"
    assert run.coverage_basis is None                    # 저장하지 않았으므로 근거도 남기지 않는다
    rows = db_session.query(CloudAccountCost).filter(CloudAccountCost.cloud_account_id == azure.id).all()
    assert [str(r.amount) for r in rows] == ["777.000000"]   # 기존 금액 보존(부분 행 저장 안 함)
    db_session.refresh(good_run)
    assert good_run.coverage_basis == "observed_only" and good_run.status == "success"   # 근거 보존
