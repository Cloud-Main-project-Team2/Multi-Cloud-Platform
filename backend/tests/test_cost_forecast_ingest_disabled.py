"""수집이 꺼진 계정이 전망·기간 비교를 막지 않는다(2026-09-28).

#136·#137에서 Azure·GCP 어댑터가 등록되면서 두 CSP 계정이 판정 대상이 됐는데, 수집 활성화 게이트
기본값이 꺼짐이라 한 번도 수집되지 않았다. 그 결측이 "하나라도 빠지면 보류" 규칙에 걸려 AWS가 이달을
빠짐없이 수집했는데도 월말 전망이 영원히 `insufficient_coverage`였다(실DB에서 사용자 3명 모두 재현).

규칙(`app/cost/query.py::split_ingest_disabled`): 수집 경로가 모두 막혀 있고 **판정 창에 관측된 날이
하나도 없는** 계정만 판정에서 빼고, 뺀 계정은 `ingest_disabled_accounts`로 응답에 남긴다.

실제 CSP는 호출하지 않는다 — 직접 심은 행/run만 쓴다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.config import get_settings
from app.cost import query as q_mod
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Credential
from app.security.credential_crypto import encrypt_credential_json

TODAY = dt.date(2026, 9, 21)
NOW = dt.datetime(2026, 9, 21, 3, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _mock_new_csp_adapters(monkeypatch):
    """azure·gcp를 "수집기는 구현돼 있다" 상태로 만든다 — 지금 main과 같은 등록 상태."""
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


@pytest.fixture(autouse=True)
def _default_gating(monkeypatch):
    """게이트 기본값(aws만 허용)으로 고정한다 — 개발자 .env가 테스트를 바꾸지 않게."""
    _set_gating(monkeypatch)
    yield
    get_settings.cache_clear()


def _set_gating(monkeypatch, *, manual="aws", auto="aws", account_ids=""):
    monkeypatch.setenv("COST_INGEST_PROVIDERS", manual)
    monkeypatch.setenv("COST_AUTO_INGEST_PROVIDERS", auto)
    monkeypatch.setenv("COST_INGEST_ACCOUNT_IDS", account_ids)
    get_settings.cache_clear()


def _account(db, user, provider="aws", ext="111122223333"):
    a = CloudAccount(user_id=user.id, provider=provider, external_account_id=ext, account_label=f"{provider}-{ext}")
    db.add(a)
    db.flush()
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    db.add(Credential(cloud_account_id=a.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
                      encryption_key_version="v1", verified=True, permission_scope={"cost_read": True}))
    db.flush()
    return a


def _rows_every_day(db, account, start, end, amount="1.000000"):
    d = start
    while d < end:
        db.add(CloudAccountCost(
            cloud_account_id=account.id, provider=account.provider, charge_category="usage", service="Svc",
            amount=Decimal(amount), currency="USD", period_start=d, period_end=d + dt.timedelta(days=1),
            as_of=NOW, source=f"{account.provider}_cost", source_record_key=f"{account.id}:{d}",
        ))
        d += dt.timedelta(days=1)
    db.flush()


def _run(db, user, account, start, end, *, basis="complete_range"):
    db.add(CostIngestionRun(
        user_id=user.id, cloud_account_id=account.id, trigger_type="manual", status="success",
        period_start=start, period_end=end, requested_at=NOW, started_at=NOW, finished_at=NOW,
        records_replaced=1, coverage_basis=basis,
    ))
    db.flush()


def _aws_complete(db, user, start=dt.date(2026, 9, 1)):
    aws = _account(db, user)
    _rows_every_day(db, aws, start, TODAY)
    _run(db, user, aws, start, TODAY)
    return aws


def _forecast(client, user, auth_header):
    resp = client.get("/api/v1/costs/summary", params={"period_start": "2026-09-01", "period_end": "2026-09-21"},
                      headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    k = resp.json()["data"]["kpis"]
    return k["forecast_month_end"], k["forecast_status"]


def test_disabled_never_collected_account_does_not_block_forecast(client, make_user, auth_header, db_session):
    """재현: AWS는 이달 완전, Azure·GCP는 게이트 기본값(꺼짐)이라 수집 0건 → 예전엔 insufficient_coverage."""
    user = make_user()
    _aws_complete(db_session, user)
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    gcp = _account(db_session, user, provider="gcp", ext="proj-1")
    db_session.commit()

    rows, st = _forecast(client, user, auth_header)

    assert st["state"] == "computed"
    assert rows[0]["amount"] == "30.000000"                     # 20일 × $1 ÷ 20 × 30
    assert st["required_accounts"] == 1
    assert st["incomplete_accounts"] == []
    assert sorted(st["ingest_disabled_accounts"], key=lambda x: x["cloud_account_id"]) == sorted([
        {"cloud_account_id": str(azure.id), "reason": "INGEST_DISABLED"},
        {"cloud_account_id": str(gcp.id), "reason": "INGEST_DISABLED"},
    ], key=lambda x: x["cloud_account_id"])


def test_account_not_in_allow_list_reports_its_own_reason(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    _aws_complete(db_session, user)
    azure = _account(db_session, user, provider="azure", ext="sub-2")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", auto="aws", account_ids="")   # CSP는 켰지만 허용 계정 없음

    _, st = _forecast(client, user, auth_header)

    assert st["state"] == "computed"
    assert st["ingest_disabled_accounts"] == [{"cloud_account_id": str(azure.id), "reason": "ACCOUNT_NOT_ENABLED"}]


def test_collectable_account_still_holds_forecast(client, make_user, auth_header, db_session, monkeypatch):
    """수동 수집이 허용된 계정의 결측은 사람이 메울 수 있다 — 기존처럼 보류한다."""
    user = make_user()
    _aws_complete(db_session, user)
    azure = _account(db_session, user, provider="azure", ext="sub-3")
    db_session.commit()
    _set_gating(monkeypatch, manual="aws,azure", auto="aws", account_ids=f"azure:{azure.id}")

    rows, st = _forecast(client, user, auth_header)

    assert rows == []
    assert st["state"] == "insufficient_coverage"
    assert st["incomplete_accounts"][0]["cloud_account_id"] == str(azure.id)
    assert st["ingest_disabled_accounts"] == []


def test_disabled_account_with_partial_data_still_holds_forecast(client, make_user, auth_header, db_session):
    """켰다가 끈 계정처럼 이달 관측이 일부 있으면 빼지 않는다 — 빼면 분자에 그 금액이 남은 채 결측
    검사만 사라져 전망이 실제보다 낮게 나온다."""
    user = make_user()
    _aws_complete(db_session, user)
    azure = _account(db_session, user, provider="azure", ext="sub-4")
    _rows_every_day(db_session, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    _run(db_session, user, azure, dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    db_session.commit()

    rows, st = _forecast(client, user, auth_header)

    assert rows == []
    assert st["state"] == "insufficient_coverage"
    assert st["ingest_disabled_accounts"] == []


def test_only_disabled_accounts_is_no_accounts_with_reason(client, make_user, auth_header, db_session):
    user = make_user()
    azure = _account(db_session, user, provider="azure", ext="sub-5")
    db_session.commit()

    rows, st = _forecast(client, user, auth_header)

    assert rows == []
    assert st["state"] == "no_accounts"
    assert st["ingest_disabled_accounts"] == [{"cloud_account_id": str(azure.id), "reason": "INGEST_DISABLED"}]


def test_disabled_never_collected_account_does_not_block_comparison(client, make_user, auth_header, db_session):
    user = make_user()
    _aws_complete(db_session, user, start=dt.date(2026, 8, 22))
    azure = _account(db_session, user, provider="azure", ext="sub-6")
    db_session.commit()

    resp = client.get("/api/v1/costs/changes",
                      params={"period_start": "2026-09-01", "period_end": "2026-09-11", "compare": "previous_period"},
                      headers=auth_header(user))
    assert resp.status_code == 200, resp.text
    cmp = resp.json()["data"]["comparability"]

    assert resp.json()["data"]["comparable"] is True, cmp["reasons"]
    assert cmp["accounts"] == []
    assert cmp["ingest_disabled_accounts"] == [{"cloud_account_id": str(azure.id), "reason": "INGEST_DISABLED"}]
