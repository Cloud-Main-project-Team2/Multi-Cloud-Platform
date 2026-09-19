"""PR 8 — `GET /cost-anomalies` · `/cost-review-items` HTTP 계약(05 §7-1·§7-2 · v1.2 §11-11)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.cost.coverage import utc_today
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, CostReviewItem, Notification

D = dt.timedelta
TODAY = utc_today()
LAST_ELIGIBLE = TODAY - D(days=4)


def _account(db, user, ext="111122223333"):
    a = CloudAccount(user_id=user.id, provider="aws", external_account_id=ext, account_label="prod-aws")
    db.add(a)
    db.flush()
    return a


def _cost(db, account, day, amount, service="AmazonEC2", currency="USD"):
    row = CloudAccountCost(
        cloud_account_id=account.id, provider="aws", charge_category="usage", service=service, amount=Decimal(amount),
        currency=currency, period_start=day, period_end=day + D(days=1), as_of=dt.datetime.now(dt.timezone.utc),
        source="aws_cost_explorer", source_record_key=f"{account.id}:{service}:{day}:{amount}",
    )
    db.add(row)
    db.flush()


def _run(db, account, start, end):
    now = dt.datetime.now(dt.timezone.utc)
    db.add(CostIngestionRun(user_id=account.user_id, cloud_account_id=account.id, trigger_type="auto", status="success",
                            period_start=start, period_end=end, requested_at=now, finished_at=now))
    db.flush()


def _spike_fixture(db, user):
    """마지막 판정일(오늘−4)에 급증 하나. 그 전 16일은 $10 평탄."""
    a = _account(db, user)
    start = LAST_ELIGIBLE - D(days=16)
    for i in range(16):
        _cost(db, a, start + D(days=i), "10")
    _cost(db, a, LAST_ELIGIBLE, "30")
    _run(db, a, start, LAST_ELIGIBLE + D(days=1))
    return a


def _period_query():
    start = LAST_ELIGIBLE - D(days=20)
    return f"period_start={start.isoformat()}&period_end={(TODAY + D(days=1)).isoformat()}"


def test_get_anomalies_contract_and_status_filter(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _spike_fixture(db_session, user)
    resp = client.get(f"/api/v1/costs/summary?{_period_query()}", headers=h)  # 같은 데이터가 화면 API에도 보인다
    assert resp.status_code == 200

    resp = client.get(f"/api/v1/cost-anomalies?{_period_query()}", headers=h)
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["rule"] == {"baseline_days": 7, "min_delta_amount": "5", "min_increase_pct": 50, "min_history_days": 12,
                         "exclude_recent_days": 3, "currency": "USD", "charge_category": ["usage"]}
    assert d["total"] == 1
    it = d["items"][0]
    assert it["source_key"] == f"{a.id}:AmazonEC2:{LAST_ELIGIBLE.isoformat()}"
    assert it["delta"] == "20.000000" and it["delta_pct"] == "200.0" and it["label"] == "원인 확인 필요"
    assert it["review"] is None and it["is_sample_data"] is False
    assert d["held"] == [] and d["unsupported_currency"] == [] and d["insufficient_history"] == []
    assert client.get("/api/v1/cost-anomalies?status=bogus", headers=h).status_code == 422


def test_post_review_item_is_idempotent_and_notifies_once(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _spike_fixture(db_session, user)
    key = f"{a.id}:AmazonEC2:{LAST_ELIGIBLE.isoformat()}"
    r1 = client.post("/api/v1/cost-review-items", json={"source_type": "cost_anomaly", "source_key": key, "note": "확인 중"}, headers=h)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/v1/cost-review-items", json={"source_type": "cost_anomaly", "source_key": key}, headers=h)
    assert r2.status_code == 200 and r2.json()["data"]["id"] == r1.json()["data"]["id"]
    assert db_session.query(CostReviewItem).filter_by(user_id=user.id).count() == 1
    notes = db_session.query(Notification).filter_by(user_id=user.id, type="cost_anomaly").all()
    assert len(notes) == 1
    p = notes[0].message_params
    assert p["delta"] == "20.000000" and p["service"] == "AmazonEC2" and p["review_item_id"] == r1.json()["data"]["id"]
    assert notes[0].message_key == "notif.cost.anomaly"

    # 급증 목록에 review가 붙는다 · status=open은 미종결만
    d = client.get(f"/api/v1/cost-anomalies?{_period_query()}", headers=h).json()["data"]
    assert d["items"][0]["review"] == {"item_id": r1.json()["data"]["id"], "status": "open", "resolution": None}


def test_post_review_item_validations(client, make_user, auth_header, db_session):
    user = make_user()
    other = make_user(email="other@example.com")
    h = auth_header(user)
    a = _spike_fixture(db_session, user)
    foreign = _account(db_session, other, ext="999")
    post = lambda key: client.post("/api/v1/cost-review-items", json={"source_type": "cost_anomaly", "source_key": key}, headers=h)
    assert post("garbage").status_code == 422
    assert post(f"{foreign.id}:AmazonEC2:{LAST_ELIGIBLE.isoformat()}").status_code == 404  # 남의 계정
    r = post(f"{a.id}:AmazonEC2:{(LAST_ELIGIBLE - D(days=1)).isoformat()}")  # 급증이 아닌 날
    assert r.status_code == 422 and r.json()["error"]["details"][0]["reason"] == "not_current_anomaly"
    r = post(f"{a.id}:AmazonEC2:{(TODAY - D(days=1)).isoformat()}")  # 최근 3일 안
    assert r.status_code == 422 and r.json()["error"]["details"][0]["reason"] == "not_yet_eligible"
    assert client.post("/api/v1/cost-review-items", json={"source_type": "optimization", "source_key": "x:y:2026-01-01"}, headers=h).status_code == 422


def test_patch_review_item_rules_and_ownership(client, make_user, auth_header, db_session):
    user = make_user()
    other = make_user(email="other@example.com")
    h = auth_header(user)
    a = _spike_fixture(db_session, user)
    key = f"{a.id}:AmazonEC2:{LAST_ELIGIBLE.isoformat()}"
    item_id = client.post("/api/v1/cost-review-items", json={"source_type": "cost_anomaly", "source_key": key}, headers=h).json()["data"]["id"]

    r = client.patch(f"/api/v1/cost-review-items/{item_id}", json={"status": "resolved"}, headers=h)
    assert r.status_code == 422  # resolution 없음
    r = client.patch(f"/api/v1/cost-review-items/{item_id}", json={"status": "investigating", "note": "보는 중"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "investigating"
    r = client.patch(f"/api/v1/cost-review-items/{item_id}", json={"status": "resolved", "resolution": "expected"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["resolved_at"] is not None
    r = client.patch(f"/api/v1/cost-review-items/{item_id}", json={"status": "open"}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"
    r = client.patch(f"/api/v1/cost-review-items/{item_id}", json={"note": "메모만"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "resolved"
    # 소유권·404 코드
    r = client.patch(f"/api/v1/cost-review-items/{item_id}", json={"note": "x"}, headers=auth_header(other))
    assert r.status_code == 404 and r.json()["error"]["code"] == "COST_REVIEW_ITEM_NOT_FOUND"
    # resolved는 목록 기본(open)에서 빠지고 급증 목록 status=open에서도 빠진다
    assert client.get("/api/v1/cost-review-items", headers=h).json()["data"]["total"] == 0
    assert client.get("/api/v1/cost-review-items?status=resolved", headers=h).json()["data"]["total"] == 1
    d = client.get(f"/api/v1/cost-anomalies?{_period_query()}", headers=h).json()["data"]
    assert d["total"] == 0
    d = client.get(f"/api/v1/cost-anomalies?{_period_query()}&status=all", headers=h).json()["data"]
    assert d["total"] == 1 and d["items"][0]["review"]["status"] == "resolved"


def test_list_review_items_filters_by_account(client, make_user, auth_header, db_session):
    user = make_user()
    h = auth_header(user)
    a = _spike_fixture(db_session, user)
    key = f"{a.id}:AmazonEC2:{LAST_ELIGIBLE.isoformat()}"
    client.post("/api/v1/cost-review-items", json={"source_type": "cost_anomaly", "source_key": key}, headers=h)
    assert client.get(f"/api/v1/cost-review-items?cloud_account_id={a.id}", headers=h).json()["data"]["total"] == 1
    assert client.get("/api/v1/cost-review-items?cloud_account_id=999999", headers=h).json()["data"]["total"] == 0
    assert client.get("/api/v1/cost-review-items?provider=azure", headers=h).json()["data"]["total"] == 0
    assert client.get("/api/v1/cost-review-items?provider=aws", headers=h).json()["data"]["total"] == 1


def test_recomputed_away_anomaly_leaves_queue_history(client, make_user, auth_header, db_session):
    """재수집 후 규칙을 못 넘게 된 날은 급증 목록에서 빠지지만 큐 항목은 남는다(D10)."""
    user = make_user()
    h = auth_header(user)
    a = _spike_fixture(db_session, user)
    key = f"{a.id}:AmazonEC2:{LAST_ELIGIBLE.isoformat()}"
    client.post("/api/v1/cost-review-items", json={"source_type": "cost_anomaly", "source_key": key}, headers=h)
    # 재수집으로 그 날 금액이 정상으로 바뀜
    row = db_session.query(CloudAccountCost).filter_by(cloud_account_id=a.id, period_start=LAST_ELIGIBLE).first()
    row.amount = Decimal("10")
    db_session.flush()
    assert client.get(f"/api/v1/cost-anomalies?{_period_query()}", headers=h).json()["data"]["total"] == 0
    assert client.get("/api/v1/cost-review-items", headers=h).json()["data"]["total"] == 1
