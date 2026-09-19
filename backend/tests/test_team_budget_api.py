"""PR 7 — 팀·예산 API 9종 HTTP 계약(docs/비용_개발문서/05_API계약.md §6 · 10 QA-14).

실제 CSP를 호출하지 않는다 — `cloud_account_costs`·`cost_ingestion_runs`에 직접 행을 심어 두고
판정만 확인한다. 날짜는 오늘을 기준으로 계산한다(예산 상태가 오늘에 의존하므로).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Team, TeamBudget

TODAY = dt.date.today()
MONTH_START = TODAY.replace(day=1)
CONFIRM = {"X-Action-Confirmed": "true"}


def _account(db, user, provider="aws", ext="111122223333", label=None):
    a = CloudAccount(user_id=user.id, provider=provider, external_account_id=ext, account_label=label)
    db.add(a)
    db.flush()
    return a


def _cost(db, account, day, amount, currency="USD", charge_category="usage"):
    row = CloudAccountCost(
        cloud_account_id=account.id, provider=account.provider, charge_category=charge_category,
        service="AmazonEC2", amount=Decimal(amount), currency=currency,
        period_start=day, period_end=day + dt.timedelta(days=1),
        as_of=dt.datetime.now(dt.timezone.utc), source="aws_cost_explorer",
        source_record_key=f"{account.id}:{day.isoformat()}:{charge_category}:{currency}",
    )
    db.add(row)
    db.flush()
    return row


def _covered(db, account, start, end):
    """[start, end) 구간을 성공한 수집 run으로 덮는다 — $0인 날은 행이 없어도 '수집됨'이어야 한다."""
    now = dt.datetime.now(dt.timezone.utc)
    run = CostIngestionRun(
        user_id=account.user_id, cloud_account_id=account.id, trigger_type="auto", status="success",
        period_start=start, period_end=end, requested_at=now, started_at=now, finished_at=now,
    )
    db.add(run)
    db.flush()
    return run


def _team(client, headers, name="운영팀", currency="USD"):
    resp = client.post("/api/v1/teams", json={"name": name, "currency": currency}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _assign(client, headers, team_id, account_ids):
    resp = client.put(f"/api/v1/teams/{team_id}/accounts", json={"cloud_account_ids": [str(i) for i in account_ids]}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _budget(client, headers, team_id, **overrides):
    body = {"period_type": "monthly", "start_date": MONTH_START.isoformat(), "limit_amount": "300"}
    body.update(overrides)
    return client.post(f"/api/v1/teams/{team_id}/budgets", json=body, headers=headers)


def _status(client, headers, team_id):
    resp = client.get(f"/api/v1/teams/{team_id}/budget-status", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# --- 팀 -----------------------------------------------------------------------------------


def test_list_teams_always_has_unassigned_even_without_teams(client, make_user, auth_header, db_session):
    user = make_user()
    a = _account(db_session, user)
    resp = client.get("/api/v1/teams", headers=auth_header(user))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["items"] == [] and data["total"] == 0
    assert data["unassigned"]["account_count"] == 1
    acc = data["unassigned"]["accounts"][0]
    assert acc["cloud_account_id"] == str(a.id)
    assert acc["currency"] is None and acc["currency_matches_team"] is None  # 수집 전엔 통화를 모른다


def test_create_team_duplicate_name_conflict(client, make_user, auth_header):
    user = make_user()
    h = auth_header(user)
    _team(client, h, name="운영팀")
    resp = client.post("/api/v1/teams", json={"name": "운영팀"}, headers=h)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


def test_team_not_found_for_other_user(client, make_user, auth_header):
    owner = make_user(email="owner@example.com")
    other = make_user(email="other@example.com")
    team = _team(client, auth_header(owner))
    resp = client.get(f"/api/v1/teams/{team['id']}/budgets", headers=auth_header(other))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TEAM_NOT_FOUND"


def test_put_accounts_one_team_per_account_conflict(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _account(db_session, user)
    t1 = _team(client, h, name="A")
    t2 = _team(client, h, name="B")
    _assign(client, h, t1["id"], [a.id])
    resp = client.put(f"/api/v1/teams/{t2['id']}/accounts", json={"cloud_account_ids": [str(a.id)]}, headers=h)
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "CONFLICT"
    assert err["details"][0]["cloud_account_id"] == str(a.id)
    assert err["details"][0]["team_id"] == t1["id"]


def test_put_accounts_removes_unlisted_and_rejects_foreign_account(client, make_user, auth_header, db_session):
    user = make_user()
    other = make_user(email="other@example.com")
    h = auth_header(user)
    a1 = _account(db_session, user, ext="1")
    a2 = _account(db_session, user, ext="2")
    foreign = _account(db_session, other, ext="3")
    t = _team(client, h)
    data = _assign(client, h, t["id"], [a1.id, a2.id])
    assert data["account_count"] == 2
    data = _assign(client, h, t["id"], [a1.id])  # a2는 미배정으로 돌아간다
    assert [x["cloud_account_id"] for x in data["accounts"]] == [str(a1.id)]
    resp = client.put(f"/api/v1/teams/{t['id']}/accounts", json={"cloud_account_ids": [str(foreign.id)]}, headers=h)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CLOUD_ACCOUNT_NOT_FOUND"


def test_patch_currency_blocked_when_team_has_collected_costs(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _account(db_session, user)
    t = _team(client, h)
    # 수집 전엔 통화 변경 허용
    resp = client.patch(f"/api/v1/teams/{t['id']}", json={"currency": "krw"}, headers=h)
    assert resp.status_code == 200 and resp.json()["data"]["currency"] == "KRW"
    _assign(client, h, t["id"], [a.id])
    _cost(db_session, a, MONTH_START, "10", currency="KRW")
    resp = client.patch(f"/api/v1/teams/{t['id']}", json={"currency": "USD"}, headers=h)
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "CONFLICT"
    assert err["details"][0] == {"field": "currency", "reason": "team_has_collected_costs"}
    # 이름만 바꾸는 건 여전히 된다
    resp = client.patch(f"/api/v1/teams/{t['id']}", json={"name": "새 이름"}, headers=h)
    assert resp.status_code == 200 and resp.json()["data"]["name"] == "새 이름"


def test_delete_team_requires_confirmation_and_keeps_accounts_and_costs(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _account(db_session, user)
    t = _team(client, h)
    _assign(client, h, t["id"], [a.id])
    _budget(client, h, t["id"])
    _cost(db_session, a, MONTH_START, "10")

    assert client.delete(f"/api/v1/teams/{t['id']}", headers=h).status_code == 428
    resp = client.delete(f"/api/v1/teams/{t['id']}", headers={**h, **CONFIRM})
    assert resp.status_code == 204

    db_session.expire_all()
    assert db_session.get(Team, int(t["id"])) is None
    assert db_session.query(TeamBudget).filter_by(team_id=int(t["id"])).count() == 0
    account = db_session.get(CloudAccount, a.id)
    assert account is not None and account.team_id is None  # 계정은 남고 미배정으로
    assert db_session.query(CloudAccountCost).filter_by(cloud_account_id=a.id).count() == 1  # 과거 비용도 남는다


# --- 예산 CRUD ------------------------------------------------------------------------------


def test_budget_validation_limit_positive_and_custom_rules(client, make_user, auth_header):
    user = make_user()
    h = auth_header(user)
    t = _team(client, h)
    assert _budget(client, h, t["id"], limit_amount="0").status_code == 422
    assert _budget(client, h, t["id"], limit_amount="-5").status_code == 422
    assert _budget(client, h, t["id"], period_type="weekly").status_code == 422
    # custom: end_date 필수 · 1년 초과 422(VALIDATION_ERROR로 흡수)
    assert _budget(client, h, t["id"], period_type="custom").status_code == 422
    resp = _budget(client, h, t["id"], period_type="custom", start_date="2026-01-01", end_date="2027-06-01")
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "VALIDATION_ERROR"
    # 반복 예산에 end_date 422
    assert _budget(client, h, t["id"], end_date="2026-12-31").status_code == 422


def test_recurring_budget_chain_conflict_and_history(client, make_user, auth_header):
    user = make_user()
    h = auth_header(user)
    t = _team(client, h)
    first = _budget(client, h, t["id"], start_date="2026-01-01", limit_amount="300")
    assert first.status_code == 201
    # 같은/이른 시작일은 활성 반복 중복 → 409 CONFLICT(existing_budget_id)
    dup = _budget(client, h, t["id"], period_type="quarterly", start_date="2026-01-01", limit_amount="500")
    assert dup.status_code == 409
    assert dup.json()["error"]["details"][0]["existing_budget_id"] == first.json()["data"]["id"]
    # 더 늦은 시작일은 새 행(이력 보존) — 기존 행은 그대로 남는다
    later = _budget(client, h, t["id"], start_date="2026-06-01", limit_amount="500")
    assert later.status_code == 201
    items = client.get(f"/api/v1/teams/{t['id']}/budgets", headers=h).json()["data"]["items"]
    assert len(items) == 2
    assert items[0]["limit_amount"] == "500.000000" and items[1]["limit_amount"] == "300.000000"


def test_custom_overlap_conflict_and_parallel_with_recurring(client, make_user, auth_header):
    user = make_user()
    h = auth_header(user)
    t = _team(client, h)
    assert _budget(client, h, t["id"], start_date="2026-01-01").status_code == 201
    c1 = _budget(client, h, t["id"], period_type="custom", start_date="2026-03-01", end_date="2026-04-01", limit_amount="100")
    assert c1.status_code == 201  # 반복과 병행 허용
    c2 = _budget(client, h, t["id"], period_type="custom", start_date="2026-03-15", end_date="2026-05-01", limit_amount="100")
    assert c2.status_code == 409
    assert c2.json()["error"]["details"][0]["reason"] == "custom_period_overlap"
    c3 = _budget(client, h, t["id"], period_type="custom", start_date="2026-04-01", end_date="2026-05-01", limit_amount="100")
    assert c3.status_code == 201  # 제외 경계라 4/1 시작은 안 겹친다


def test_patch_budget_only_upcoming_and_locked_fields(client, make_user, auth_header):
    user = make_user()
    h = auth_header(user)
    t = _team(client, h)
    started = _budget(client, h, t["id"], start_date=MONTH_START.isoformat(), limit_amount="300").json()["data"]
    upcoming = _budget(client, h, t["id"], start_date=(TODAY + dt.timedelta(days=40)).isoformat(), limit_amount="400").json()["data"]

    # 이미 시작된 예산은 409
    resp = client.patch(f"/api/v1/team-budgets/{started['id']}", json={"limit_amount": "350"}, headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["details"][0]["reason"] == "budget_already_started"
    # 예정 예산의 오타 수정은 200
    resp = client.patch(f"/api/v1/team-budgets/{upcoming['id']}", json={"limit_amount": "450"}, headers=h)
    assert resp.status_code == 200 and resp.json()["data"]["limit_amount"] == "450.000000"
    # start_date를 바꾸려 하면 409 + "새 행을 만들라"
    resp = client.patch(f"/api/v1/team-budgets/{upcoming['id']}", json={"start_date": "2027-01-01"}, headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["details"][0]["reason"] == "create_new_budget"
    # 삭제는 확인 헤더
    assert client.delete(f"/api/v1/team-budgets/{upcoming['id']}", headers=h).status_code == 428
    assert client.delete(f"/api/v1/team-budgets/{upcoming['id']}", headers={**h, **CONFIRM}).status_code == 204
    resp = client.delete(f"/api/v1/team-budgets/{upcoming['id']}", headers={**h, **CONFIRM})
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "TEAM_BUDGET_NOT_FOUND"


# --- budget-status (QA-14) -----------------------------------------------------------------


def test_status_no_budget_is_not_zero(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _account(db_session, user)
    t = _team(client, h)
    _assign(client, h, t["id"], [a.id])
    s = _status(client, h, t["id"])
    assert s["computable"] is False and s["reason_code"] == "NO_BUDGET"
    assert s["ratio_pct"] is None and s["budget"] is None


def test_status_zero_usage_is_zero_point_zero(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _account(db_session, user)
    t = _team(client, h)
    _assign(client, h, t["id"], [a.id])
    _covered(db_session, a, MONTH_START, TODAY + dt.timedelta(days=1))
    assert _budget(client, h, t["id"]).status_code == 201
    s = _status(client, h, t["id"])
    assert s["computable"] is True and s["reason_code"] is None
    assert s["ratio_pct"] == "0.0"
    assert s["usage"]["amount"] == "0" or Decimal(s["usage"]["amount"]) == 0
    assert s["usage"]["basis"] == "usage_before_credits"
    assert s["budget"]["period_state"] == "in_progress"
    assert s["budget"]["period_start"] == MONTH_START.isoformat()


def test_status_over_budget_not_capped(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _account(db_session, user)
    t = _team(client, h)
    _assign(client, h, t["id"], [a.id])
    _covered(db_session, a, MONTH_START, TODAY + dt.timedelta(days=1))
    _cost(db_session, a, MONTH_START, "312.40")
    _cost(db_session, a, MONTH_START, "-20", charge_category="credit")  # net에만 반영
    assert _budget(client, h, t["id"], limit_amount="300").status_code == 201
    s = _status(client, h, t["id"])
    assert s["computable"] is True
    assert s["ratio_pct"] == "104.1"
    assert Decimal(s["usage"]["amount"]) == Decimal("312.40")
    assert Decimal(s["usage"]["net_amount"]) == Decimal("292.40")
    assert [th["crossed"] for th in s["thresholds"]] == [True, True]
    assert [th["notified"] for th in s["thresholds"]] == [True, True]  # 예산 생성 직후 평가돼 알림이 생겼다


def test_status_missing_day_is_not_computable(client, make_user, auth_header, db_session):
    if TODAY.day < 3:
        return  # 이번 달 1~2일엔 "결측 하루"를 만들 수 없다
    user = make_user()
    h = auth_header(user)
    a = _account(db_session, user)
    t = _team(client, h)
    _assign(client, h, t["id"], [a.id])
    # 1일은 행이 있고 2일부터는 수집이 없다
    _cost(db_session, a, MONTH_START, "10")
    assert _budget(client, h, t["id"]).status_code == 201
    s = _status(client, h, t["id"])
    assert s["computable"] is False and s["reason_code"] == "MISSING_DAYS"
    assert s["ratio_pct"] is None
    assert Decimal(s["usage"]["amount"]) == Decimal("10")  # 사용액은 보이되 판정만 안 한다


def test_status_currency_mismatch_lists_excluded_accounts(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    usd = _account(db_session, user, ext="1", label="USD 계정")
    krw = _account(db_session, user, ext="2", label="KRW 계정")
    t = _team(client, h)
    _assign(client, h, t["id"], [usd.id, krw.id])
    _covered(db_session, usd, MONTH_START, TODAY + dt.timedelta(days=1))
    _covered(db_session, krw, MONTH_START, TODAY + dt.timedelta(days=1))
    _cost(db_session, usd, MONTH_START, "10", currency="USD")
    _cost(db_session, krw, MONTH_START, "5000", currency="KRW")
    assert _budget(client, h, t["id"]).status_code == 201
    s = _status(client, h, t["id"])
    assert s["computable"] is False and s["reason_code"] == "CURRENCY_MISMATCH"
    assert s["ratio_pct"] is None
    assert [e["cloud_account_id"] for e in s["excluded_accounts"]] == [str(krw.id)]
    assert s["excluded_accounts"][0]["reason"] == "currency_mismatch"
    # 팀 목록에서도 currency_matches_team=false로 보이되 팀에서 빠지진 않는다
    team = client.get("/api/v1/teams", headers=h).json()["data"]["items"][0]
    by_id = {x["cloud_account_id"]: x for x in team["accounts"]}
    assert by_id[str(krw.id)]["currency_matches_team"] is False
    assert by_id[str(usd.id)]["currency_matches_team"] is True
    assert team["account_count"] == 2


def test_status_no_accounts_and_unsupported(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    t = _team(client, h)
    assert _budget(client, h, t["id"]).status_code == 201
    assert _status(client, h, t["id"])["reason_code"] == "NO_ACCOUNTS"
    azure = _account(db_session, user, provider="azure", ext="sub-1")
    _assign(client, h, t["id"], [azure.id])
    s = _status(client, h, t["id"])
    assert s["computable"] is False and s["reason_code"] == "UNSUPPORTED"


def test_status_custom_overrides_recurring_and_upcoming_state(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _account(db_session, user)
    t = _team(client, h)
    _assign(client, h, t["id"], [a.id])
    _covered(db_session, a, MONTH_START - dt.timedelta(days=31), TODAY + dt.timedelta(days=1))
    assert _budget(client, h, t["id"], start_date="2026-01-01", limit_amount="300").status_code == 201
    custom_end = TODAY + dt.timedelta(days=10)
    custom = _budget(client, h, t["id"], period_type="custom", start_date=TODAY.isoformat(), end_date=custom_end.isoformat(), limit_amount="50")
    assert custom.status_code == 201
    s = _status(client, h, t["id"])
    assert s["budget"]["id"] == custom.json()["data"]["id"]  # 진행 중 custom이 override
    assert s["budget"]["period_type"] == "custom" and s["budget"]["period_end"] == custom_end.isoformat()

    # 예정만 있는 팀은 upcoming
    t2 = _team(client, h, name="예정팀")
    _assign(client, h, t2["id"], [])
    b = _budget(client, h, t2["id"], start_date=(TODAY + dt.timedelta(days=60)).isoformat())
    assert b.status_code == 201
    s2 = client.get(f"/api/v1/teams/{t2['id']}/budgets", headers=h).json()["data"]["items"][0]
    assert s2["state"] == "upcoming"


# --- /costs/* team_id 필터 ------------------------------------------------------------------


def test_costs_summary_team_filter_and_unassigned(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a1 = _account(db_session, user, ext="1")
    a2 = _account(db_session, user, ext="2")
    t = _team(client, h)
    _assign(client, h, t["id"], [a1.id])
    _cost(db_session, a1, MONTH_START, "10")
    _cost(db_session, a2, MONTH_START, "20")

    def total(query):
        resp = client.get("/api/v1/costs/summary" + query, headers=h)
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]["kpis"]["mtd_actual"]
        return Decimal(rows[0]["amount"]) if rows else Decimal("0")

    assert total("") == Decimal("30")
    assert total(f"?team_id={t['id']}") == Decimal("10")
    assert total("?team_id=unassigned") == Decimal("20")
    assert total(f"?team_id={t['id']}&team_id=unassigned") == Decimal("30")
    assert total("?team_id=999999") == Decimal("0")  # 없는 팀 → 빈 집합(전체가 아니다)
    resp = client.get("/api/v1/costs/summary?team_id=abc", headers=h)
    assert resp.status_code == 422
