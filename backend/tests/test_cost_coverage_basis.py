"""A-2 — 관측된 금액과 "판정에 쓸 수 있는가"를 분리한다(2026-09-23).

핵심 반례: Azure에서 **조회 기간의 모든 날짜에 행이 있어도** 스토리지 비용이 아직 안 왔을 수 있다.
그래서 `missing_count == 0`만으로 전망·비교·예산·급증을 켜지 않는다. 근거는 run에 기록한
`coverage_basis`이고, 날짜별 근거는 **그 날을 덮는 성공 run 중 가장 나중에 저장한 것**이 정한다.

실제 CSP는 호출하지 않는다 — 모의 어댑터와 직접 심은 행/run만 쓴다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.cost import query as q_mod
from app.cost.coverage import (
    BASIS_COMPLETE_RANGE,
    BASIS_OBSERVED_ONLY,
    analysis_ready_days,
    covered_days,
    day_basis_map,
    resolve_basis,
)
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Credential
from app.security.credential_crypto import encrypt_credential_json

TODAY = dt.date(2026, 9, 21)
NOW = dt.datetime(2026, 9, 21, 3, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _mock_new_csp_adapters(monkeypatch):
    """azure·gcp를 "수집기는 구현돼 있다" 상태로 만든다 — A-2는 지원 여부가 아니라 **근거**를 다룬다.
    실제 CSP는 호출하지 않는다(어댑터 객체는 만들어지지도 않는다)."""
    import app.cost as cost_pkg

    class _Unused:
        def fetch(self, *a, **k):  # pragma: no cover - 호출되지 않아야 한다
            raise AssertionError("테스트에서 실제 수집을 부르면 안 된다")

    monkeypatch.setitem(cost_pkg.COST_ADAPTERS, "azure", _Unused)
    monkeypatch.setitem(cost_pkg.COST_ADAPTERS, "gcp", _Unused)


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    monkeypatch.setattr(q_mod, "utc_today", lambda: TODAY)
    import app.cost.coverage as cov
    monkeypatch.setattr(cov, "utc_today", lambda: TODAY)


def _account(db, user, provider="aws", ext="111122223333"):
    a = CloudAccount(user_id=user.id, provider=provider, external_account_id=ext, account_label=f"{provider}-{ext}")
    db.add(a)
    db.flush()
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    db.add(Credential(cloud_account_id=a.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
                      encryption_key_version="v1", verified=True, permission_scope={"cost_read": True}))
    db.flush()
    return a


def _row(db, account, day, amount="1.000000", currency="USD", service="Svc"):
    db.add(CloudAccountCost(
        cloud_account_id=account.id, provider=account.provider, charge_category="usage", service=service,
        amount=Decimal(amount), currency=currency, period_start=day, period_end=day + dt.timedelta(days=1),
        as_of=NOW, source=f"{account.provider}_cost_explorer",
        source_record_key=f"{account.id}:{day}:{service}",
    ))
    db.flush()


def _rows_every_day(db, account, start, end, **kw):
    d = start
    while d < end:
        _row(db, account, d, **kw)
        d += dt.timedelta(days=1)


def _run(db, user, account, start, end, *, basis, status="success", finished=NOW, records=1):
    r = CostIngestionRun(
        user_id=user.id, cloud_account_id=account.id, trigger_type="manual", status=status,
        period_start=start, period_end=end, requested_at=finished, started_at=finished,
        finished_at=finished, records_replaced=records, coverage_basis=basis,
    )
    db.add(r)
    db.flush()
    return r


def _summary(client, user, auth_header, **params):
    base = {"period_start": "2026-09-01", "period_end": "2026-09-21"}
    base.update(params)
    resp = client.get("/api/v1/costs/summary", params=base, headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _acc(data, account):
    return [a for a in data["accounts"] if a["cloud_account_id"] == str(account.id)][0]


# --- 1·2·13: 모든 날짜에 행이 있어도 판정 보류 · 금액은 유지 · 개수 관계 ------------------------


def test_observed_only_holds_judgment_even_with_rows_every_day(client, make_user, auth_header, db_session):
    """반례 그대로 — Azure에 20일치 행이 다 있고 결측 0인데, 근거가 약하면 전망을 내지 않는다.
    금액은 그대로 나오고(수집 실패로 위장하지 않는다) `covered + missing_count = days`도 성립한다."""
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    _rows_every_day(db_session, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    _run(db_session, user, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY)
    db_session.commit()

    d = _summary(client, user, auth_header)
    acc = _acc(d, azure)

    assert acc["actual"] == "20.000000"                      # ② 받은 금액 유지
    assert d["kpis"]["mtd_actual"][0]["amount"] == "20.000000"
    cov = acc["coverage"]
    assert cov["covered"] == 20 and cov["missing_count"] == 0 and cov["days"] == 20   # ⑬
    assert cov["basis"] == "observed_only" and cov["analysis_ready"] is False
    assert d["kpis"]["forecast_month_end"] == []             # ① 판정 보류
    assert d["kpis"]["forecast_status"]["state"] == "coverage_unverified"
    assert d["kpis"]["forecast_status"]["unverified_accounts"] == [{"cloud_account_id": str(azure.id)}]


def test_aws_same_shape_still_computes(client, make_user, auth_header, db_session):
    """④ 같은 모양의 AWS(레거시 NULL basis)는 기존대로 판정한다 — 이번 변경이 AWS를 건드리지 않는다."""
    user = make_user()
    aws = _account(db_session, user)
    _rows_every_day(db_session, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=None)
    db_session.commit()

    d = _summary(client, user, auth_header)

    assert _acc(d, aws)["coverage"]["basis"] == "complete_range"
    assert _acc(d, aws)["coverage"]["analysis_ready"] is True
    assert d["kpis"]["forecast_status"]["state"] == "computed"
    assert d["kpis"]["forecast_month_end"][0]["amount"] == "30.000000"


# --- 3: 신규 CSP 정상 빈 응답 — 0원으로 단정하지 않는다 ----------------------------------------


def test_observed_only_empty_response_is_not_confirmed_zero(client, make_user, auth_header, db_session):
    user = make_user()
    gcp = _account(db_session, user, provider="gcp", ext="proj-1")
    _run(db_session, user, gcp, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY, records=0)
    db_session.commit()

    d = _summary(client, user, auth_header)
    acc = _acc(d, gcp)

    assert acc["status"] == "CONNECTED_EMPTY"          # 수집 요청은 정상 완료
    assert acc["actual"] is None                       # 0원이라고 말하지 않는다
    assert acc["currency"] is None                     # 통화를 모르면 USD를 붙이지 않는다
    cov = acc["coverage"]
    assert cov["covered"] == 0 and cov["missing_count"] == 20 and cov["days"] == 20   # ⑬ 관계 유지
    assert cov["basis"] is None and cov["analysis_ready"] is False


def test_aws_empty_response_stays_confirmed_zero(client, make_user, auth_header, db_session):
    """④ AWS의 '확인된 0원'은 그대로다 — 성공 run 범위가 근거."""
    user = make_user()
    aws = _account(db_session, user)
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE, records=0)
    db_session.commit()

    cov = _acc(_summary(client, user, auth_header), aws)["coverage"]
    assert cov["covered"] == 20 and cov["missing_count"] == 0
    assert cov["basis"] == "complete_range" and cov["analysis_ready"] is True


# --- 5·6·7: 최신 저장 결과와 근거의 일치 --------------------------------------------------------


def test_overlapping_observed_only_recollection_removes_strong_basis_only_on_overlap(
    client, make_user, auth_header, db_session
):
    """⑤ complete_range로 받은 뒤 일부 구간을 observed_only로 재수집하면 **겹친 날만** 근거가 약해진다."""
    user = make_user()
    acct = _account(db_session, user, provider="azure", ext="sub-2")
    _rows_every_day(db_session, acct, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    _run(db_session, user, acct, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE,
         finished=NOW - dt.timedelta(hours=5))
    _run(db_session, user, acct, dt.date(2026, 9, 10), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY,
         finished=NOW)
    db_session.commit()

    bases = day_basis_map(db_session, acct.id, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    assert bases[dt.date(2026, 9, 9)] == BASIS_COMPLETE_RANGE
    assert bases[dt.date(2026, 9, 10)] == BASIS_OBSERVED_ONLY
    ready = analysis_ready_days(db_session, acct.id, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    assert ready == {dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(9)}
    # ⑨ 혼재 기간의 요약은 보수적으로 observed_only — 기간 전체를 최신 run 하나로 설명하지 않는다
    cov = _acc(_summary(client, user, auth_header), acct)["coverage"]
    assert cov["basis"] == "observed_only" and cov["missing_count"] == 0 and cov["analysis_ready"] is False


def test_storage_order_not_request_order_decides_basis(client, make_user, auth_header, db_session):
    """⑥ 요청 순서(id)와 저장 완료 순서(finished_at)가 다르면 **저장 순서**가 이긴다.
    행 교체와 run 기록이 같은 트랜잭션이라 finished_at이 곧 저장 시각이다."""
    user = make_user()
    acct = _account(db_session, user, provider="azure", ext="sub-3")
    _rows_every_day(db_session, acct, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    # 먼저 만들어졌지만(id 작음) 나중에 저장된 run이 complete_range다
    _run(db_session, user, acct, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE,
         finished=NOW)
    _run(db_session, user, acct, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY,
         finished=NOW - dt.timedelta(hours=3))
    db_session.commit()

    cov = _acc(_summary(client, user, auth_header), acct)["coverage"]
    assert cov["basis"] == "complete_range" and cov["analysis_ready"] is True


def test_failed_and_partial_runs_do_not_change_basis_or_amounts(client, make_user, auth_header, db_session):
    """⑦ 실패·부분 응답 run은 행을 쓰지 않으므로 이전 금액도 근거도 건드리지 않는다."""
    user = make_user()
    aws = _account(db_session, user)
    _rows_every_day(db_session, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE,
         finished=NOW - dt.timedelta(hours=5))
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=None,
         status="partial_success", finished=NOW, records=0)
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=None,
         status="failed", finished=NOW, records=0)
    db_session.commit()

    acc = _acc(_summary(client, user, auth_header), aws)
    assert acc["actual"] == "20.000000"                    # 금액 유지
    assert acc["coverage"]["basis"] == "complete_range"    # 근거 유지
    assert acc["coverage"]["analysis_ready"] is True


# --- 8: 비AWS NULL은 확대되지 않는다 ------------------------------------------------------------


def test_non_aws_null_basis_is_observed_only(client, make_user, auth_header, db_session):
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-4")
    _rows_every_day(db_session, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    _run(db_session, user, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=None)
    db_session.commit()

    assert resolve_basis(None, "azure") == BASIS_OBSERVED_ONLY
    assert resolve_basis("weird-value", "aws") == BASIS_OBSERVED_ONLY     # 모르는 값도 확대 금지
    cov = _acc(_summary(client, user, auth_header), azure)["coverage"]
    assert cov["basis"] == "observed_only" and cov["analysis_ready"] is False


def test_legacy_rows_without_any_run_keep_aws_behaviour(client, make_user, auth_header, db_session):
    """레거시(시드 등): 성공 run 없이 행만 있는 AWS 계정은 예전처럼 판정 가능해야 한다."""
    user = make_user()
    aws = _account(db_session, user, ext="legacy")
    _rows_every_day(db_session, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    db_session.commit()

    assert covered_days(db_session, aws.id, dt.date(2026, 9, 1), dt.date(2026, 9, 21)) == \
        analysis_ready_days(db_session, aws.id, dt.date(2026, 9, 1), dt.date(2026, 9, 21))
    assert _summary(client, user, auth_header)["kpis"]["forecast_status"]["state"] == "computed"


# --- 기간 비교 · covered_through ----------------------------------------------------------------


def test_comparison_holds_with_coverage_unverified_but_keeps_amounts(client, make_user, auth_header, db_session):
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-5")
    _rows_every_day(db_session, azure, dt.date(2026, 8, 12), dt.date(2026, 9, 21))
    _run(db_session, user, azure, dt.date(2026, 8, 12), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY)
    db_session.commit()

    resp = client.get("/api/v1/costs/changes",
                      params={"period_start": "2026-09-01", "period_end": "2026-09-11", "compare": "previous_period"},
                      headers=auth_header(user))
    d = resp.json()["data"]

    assert d["comparable"] is False
    assert "COVERAGE_UNVERIFIED" in d["comparability"]["reasons"]
    assert d["comparability"]["current_covered"] is True          # 결측이 아니라 근거 부족이다
    assert d["totals"]["current"] == "10.000000"                  # 받은 금액은 그대로


def test_collection_status_hides_covered_through_for_observed_only(client, make_user, auth_header, db_session):
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-6")
    aws = _account(db_session, user, ext="aws-1")
    _run(db_session, user, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY)
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE)
    db_session.commit()

    resp = client.get("/api/v1/costs/collection-status",
                      params={"period_start": "2026-09-01", "period_end": "2026-09-21"},
                      headers=auth_header(user))
    items = {i["cloud_account_id"]: i for i in resp.json()["data"]["items"]}

    assert items[str(azure.id)]["covered_through"] is None        # 요청 종료일을 확인 범위처럼 주지 않는다
    assert items[str(aws.id)]["covered_through"] == "2026-09-20"


# --- 10·14: 팀 예산 — 팀 구성별 구분 · 근거 부족이면 새 알림을 만들지 않는다 ---------------------


def _team_setup(db, user, *, accounts, currency="USD", limit="300"):
    from app.models import Team, TeamBudget

    team = Team(user_id=user.id, name="팀", currency=currency)
    db.add(team)
    db.flush()
    for a in accounts:
        a.team_id = team.id
    db.add(TeamBudget(team_id=team.id, period_type="monthly", start_date=dt.date(2026, 9, 1),
                      limit_amount=Decimal(limit), currency=currency))
    db.flush()
    return team


def test_aws_only_team_still_computes_budget(client, make_user, auth_header, db_session):
    """⑩ AWS 전용 팀은 영향 없음 — 기존대로 소진율을 낸다."""
    from app.cost.budget import compute_budget_status

    user = make_user()
    aws = _account(db_session, user, ext="aws-team")
    _rows_every_day(db_session, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="5.000000")
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE)
    team = _team_setup(db_session, user, accounts=[aws])
    db_session.commit()

    out = compute_budget_status(db_session, team, TODAY)

    assert out["computable"] is True and out["reason_code"] is None
    assert out["ratio_pct"] is not None


def test_mixed_team_with_observed_only_holds_but_keeps_amounts(client, make_user, auth_header, db_session):
    """⑩ 같은 통화의 AWS + 신규 CSP 혼재 팀 → 판정만 보류. 예산·한도·관측 금액은 그대로 남는다."""
    from app.cost.budget import compute_budget_status

    user = make_user()
    aws = _account(db_session, user, ext="aws-mixed")
    azure = _account(db_session, user, provider="azure", ext="sub-mixed")
    _rows_every_day(db_session, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="5.000000")
    _rows_every_day(db_session, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="1.000000")
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE)
    _run(db_session, user, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY)
    team = _team_setup(db_session, user, accounts=[aws, azure])
    db_session.commit()

    out = compute_budget_status(db_session, team, TODAY)

    assert out["computable"] is False
    assert out["reason_code"] == "COVERAGE_UNVERIFIED"
    assert out["budget"] is not None and out["budget"]["limit_amount"] == "300.000000"   # 한도는 그대로
    assert out["usage"] is not None                                                      # 관측 금액도 그대로


def test_currency_excluded_account_does_not_trigger_new_reason(client, make_user, auth_header, db_session):
    """⑩ 통화가 달라 이미 제외된 계정은 새 사유로 끌어들이지 않는다 — 기존 CURRENCY_MISMATCH 유지."""
    from app.cost.budget import compute_budget_status

    user = make_user()
    aws = _account(db_session, user, ext="aws-krw-team")
    krw = _account(db_session, user, ext="krw-acct")
    _rows_every_day(db_session, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="5.000000")
    _rows_every_day(db_session, krw, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="1000.000000", currency="KRW")
    _run(db_session, user, aws, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE)
    _run(db_session, user, krw, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_COMPLETE_RANGE)
    team = _team_setup(db_session, user, accounts=[aws, krw], currency="USD")
    db_session.commit()

    out = compute_budget_status(db_session, team, TODAY)

    assert out["reason_code"] == "CURRENCY_MISMATCH"


def test_no_budget_notification_when_coverage_unverified(client, make_user, auth_header, db_session):
    """⑭ 근거가 부족하면 임계 알림을 새로 만들지 않는다(판정 자체를 하지 않으므로)."""
    from app.cost.notify import evaluate_for_account
    from app.models import Notification

    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-notify")
    _rows_every_day(db_session, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21), amount="100.000000")
    _run(db_session, user, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY)
    _team_setup(db_session, user, accounts=[azure], limit="300")
    db_session.commit()

    evaluate_for_account(db_session, azure)
    db_session.commit()

    assert db_session.query(Notification).filter_by(user_id=user.id, type="budget_threshold").count() == 0


def test_no_anomaly_items_when_coverage_unverified(client, make_user, auth_header, db_session):
    """⑭ 급증도 근거 없는 날로는 판정하지 않는다 — 신규 항목·알림이 생기지 않는다."""
    from app.cost.anomaly import evaluate_account_all_stored

    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-anomaly")
    _rows_every_day(db_session, azure, dt.date(2026, 8, 1), dt.date(2026, 9, 10), amount="1.000000")
    _row(db_session, azure, dt.date(2026, 9, 10), amount="500.000000")   # 명백한 급증
    _run(db_session, user, azure, dt.date(2026, 8, 1), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY)
    db_session.commit()

    ev = evaluate_account_all_stored(db_session, azure, today=TODAY)

    assert ev.items == []


# --- 3(보완): 급증 보류 사유 — "이력 부족"과 "근거 부족"을 구분한다 -----------------------------


def test_anomaly_holds_with_coverage_unverified_not_insufficient_history(
    client, make_user, auth_header, db_session
):
    """금액은 충분히 오래 관측됐는데 완전성 근거가 없어 판정하지 못한 경우, "이력 0일/이력 부족"이
    아니라 **근거 부족**으로 설명한다. 사용자가 "아직 데이터가 없구나"로 잘못 읽지 않게."""
    from app.cost.anomaly import HELD_COVERAGE_UNVERIFIED, evaluate_account_all_stored

    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-held")
    _rows_every_day(db_session, azure, dt.date(2026, 8, 1), dt.date(2026, 9, 15), amount="1.000000")
    _run(db_session, user, azure, dt.date(2026, 8, 1), dt.date(2026, 9, 21), basis=BASIS_OBSERVED_ONLY)
    db_session.commit()

    ev = evaluate_account_all_stored(db_session, azure, today=TODAY)

    assert ev.items == []                                   # 항목·알림 없음
    assert ev.insufficient_history is None                  # "이력 부족"이라고 말하지 않는다
    assert ev.held, "근거 부족은 held로 설명해야 한다"
    assert {h["reason"] for h in ev.held} == {HELD_COVERAGE_UNVERIFIED}


def test_anomaly_still_reports_insufficient_history_for_new_account(client, make_user, auth_header, db_session):
    """반대 방향 — 관측 이력 자체가 짧으면 예전처럼 '이력 부족'이다(근거 부족과 섞지 않는다)."""
    from app.cost.anomaly import evaluate_account_all_stored

    user = make_user()
    aws = _account(db_session, user, ext="aws-new")
    _rows_every_day(db_session, aws, dt.date(2026, 9, 14), dt.date(2026, 9, 17), amount="1.000000")
    _run(db_session, user, aws, dt.date(2026, 9, 14), dt.date(2026, 9, 17), basis=BASIS_COMPLETE_RANGE)
    db_session.commit()

    ev = evaluate_account_all_stored(db_session, aws, today=TODAY)

    assert ev.items == []
    assert ev.insufficient_history is not None
    assert all(h["reason"] != "coverage_unverified" for h in ev.held)
