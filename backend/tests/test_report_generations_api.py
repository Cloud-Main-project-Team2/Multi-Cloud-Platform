"""POST/GET/DELETE /reports 검증(2026-09-19) — 보고서 "생성 이력" 실저장.

같은 조건(period_type·period_from·period_to·클라우드 조합)으로 다시 생성해도 새 행이
쌓이지 않고 `generated_at`만 갱신되는지(중복 방지), 사용자별로 격리되는지 확인한다.

`cost_snapshot` 관련 테스트는 `app.routers.reports.build_cost_snapshot`을 monkeypatch해서
실제 CostQuery 계산 없이 "생성 시점에 고정 저장되고 조회 시 재계산하지 않는다"는 계약만
검증한다(실제 계산 로직 자체는 비용 파트 소관 — app/cost/query.py의 기존 테스트가 담당).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import app.routers.reports as reports_router
from app.models import CloudAccount, CloudAccountCost, Notification, ReportGeneration


def _make_account(db_session, user, provider="aws", external_account_id="111122223333"):
    account = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id)
    db_session.add(account)
    db_session.flush()
    return account


def _add_cost_row(db_session, account, day, amount, currency="USD", service="AmazonEC2"):
    row = CloudAccountCost(
        cloud_account_id=account.id, provider=account.provider, charge_category="usage",
        service=service, amount=Decimal(amount), currency=currency,
        period_start=day, period_end=day + dt.timedelta(days=1),
        as_of=dt.datetime.now(dt.timezone.utc), source="aws_cost_explorer",
        source_record_key=f"{account.provider}:{account.external_account_id}:{day.isoformat()}:{service}:usage:{currency}",
    )
    db_session.add(row)
    db_session.flush()
    return row

_PAYLOAD = {
    "period_type": "WEEKLY",
    "period_from": "2026-09-13",
    "period_to": "2026-09-19",
    "clouds": ["aws", "azure", "gcp"],
}


def test_create_report_generation(client, make_user, auth_header, db_session):
    user = make_user()
    resp = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["period_type"] == "WEEKLY"
    assert data["period_from"] == "2026-09-13"
    assert set(data["clouds"]) == {"aws", "azure", "gcp"}

    row = db_session.query(ReportGeneration).filter_by(user_id=user.id).one()
    assert row.providers == "aws,azure,gcp"  # 정렬된 canonical 형태로 저장된다


def test_create_report_generation_notifies_success(client, make_user, auth_header, db_session):
    """"생성하기"는 요청 안에서 즉시 끝나 별도 job이 없으므로(4차 항목 2), 사용자가 결과 탭을
    기다리지 못하고 페이지를 벗어나도 알림함에서 확인할 수 있어야 한다."""
    user = make_user()
    resp = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    report_id = resp.json()["data"]["id"]

    notif = db_session.query(Notification).filter_by(user_id=user.id, type="report_generated").one()
    assert notif.reference_type == "report_generation"
    assert str(notif.reference_id) == report_id
    assert notif.message_params["period_from"] == "2026-09-13"
    assert notif.message_params["period_to"] == "2026-09-19"


def test_recreating_same_conditions_does_not_duplicate(client, make_user, auth_header, db_session):
    user = make_user()
    first = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user)).json()["data"]

    # 클라우드 순서만 다르게 다시 생성 — canonical 정규화 덕분에 같은 조건으로 인식돼야 한다.
    reordered = dict(_PAYLOAD, clouds=["gcp", "aws", "azure"])
    second = client.post("/api/v1/reports", json=reordered, headers=auth_header(user)).json()["data"]

    assert first["id"] == second["id"]  # 새 행이 아니라 같은 행
    rows = db_session.query(ReportGeneration).filter_by(user_id=user.id).all()
    assert len(rows) == 1
    assert second["generated_at"] >= first["generated_at"]


def test_different_period_creates_a_new_row(client, make_user, auth_header, db_session):
    user = make_user()
    client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    later = dict(_PAYLOAD, period_from="2026-09-20", period_to="2026-09-26")
    client.post("/api/v1/reports", json=later, headers=auth_header(user))

    rows = db_session.query(ReportGeneration).filter_by(user_id=user.id).all()
    assert len(rows) == 2  # 기간이 다르면 실제로 다른 보고서다 — 중복이 아니다


def test_list_orders_newest_generated_first(client, make_user, auth_header):
    user = make_user()
    older = dict(_PAYLOAD, period_from="2026-09-06", period_to="2026-09-12")
    client.post("/api/v1/reports", json=older, headers=auth_header(user))
    client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))

    items = client.get("/api/v1/reports", headers=auth_header(user)).json()["data"]["items"]
    assert len(items) == 2
    assert items[0]["period_from"] == "2026-09-13"  # 가장 최근에 생성한 것이 먼저


def test_list_is_isolated_per_user(client, make_user, auth_header):
    user_a = make_user(email="a@example.com")
    user_b = make_user(email="b@example.com")
    client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user_a))

    assert client.get("/api/v1/reports", headers=auth_header(user_b)).json()["data"]["items"] == []
    assert len(client.get("/api/v1/reports", headers=auth_header(user_a)).json()["data"]["items"]) == 1


def test_delete_removes_row(client, make_user, auth_header, db_session):
    user = make_user()
    created = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user)).json()["data"]

    resp = client.delete(f"/api/v1/reports/{created['id']}", headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True
    assert db_session.query(ReportGeneration).filter_by(user_id=user.id).count() == 0


def test_delete_another_users_report_returns_404(client, make_user, auth_header):
    user_a = make_user(email="a@example.com")
    user_b = make_user(email="b@example.com")
    created = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user_a)).json()["data"]

    resp = client.delete(f"/api/v1/reports/{created['id']}", headers=auth_header(user_b))
    assert resp.status_code == 404


def test_delete_nonexistent_report_returns_404(client, make_user, auth_header):
    user = make_user()
    resp = client.delete("/api/v1/reports/999999", headers=auth_header(user))
    assert resp.status_code == 404


def test_unknown_cloud_is_rejected(client, make_user, auth_header):
    user = make_user()
    bad = dict(_PAYLOAD, clouds=["aws", "oracle"])
    resp = client.post("/api/v1/reports", json=bad, headers=auth_header(user))
    assert resp.status_code == 422


def test_period_from_after_period_to_is_rejected(client, make_user, auth_header):
    user = make_user()
    bad = dict(_PAYLOAD, period_from="2026-09-19", period_to="2026-09-13")
    resp = client.post("/api/v1/reports", json=bad, headers=auth_header(user))
    assert resp.status_code == 422


def test_cost_snapshot_is_stored_and_uses_exclusive_end_date(client, make_user, auth_header, monkeypatch):
    """비용 API는 period_end가 exclusive다(§6) — 화면 09-13~09-19(포함)는 09-20으로 넘어가야
    한다. build_cost_snapshot 호출 인자로 그 변환이 실제로 일어나는지 확인한다."""
    user = make_user()
    seen = {}

    def fake_snapshot(db, u, period_from, period_to, providers):
        seen["args"] = (period_from, period_to, providers)
        return {"summary": {"fake": True}}

    monkeypatch.setattr(reports_router, "build_cost_snapshot", fake_snapshot)

    resp = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["cost_snapshot"] == {"summary": {"fake": True}}
    # build_cost_snapshot에는 화면 표시값(inclusive) 그대로 넘긴다 — exclusive 변환은
    # report_cost.py 내부 책임이라 라우터가 다시 하지 않는다.
    from datetime import date
    assert seen["args"] == (date(2026, 9, 13), date(2026, 9, 19), ["aws", "azure", "gcp"])


def test_reopening_report_does_not_recompute_cost_snapshot(client, make_user, auth_header, monkeypatch):
    """생성 시점 스냅샷이 고정돼야 한다 — GET으로 다시 열어도 build_cost_snapshot을 또 부르면
    안 된다(재수집으로 실제 비용이 바뀌어도 이미 만든 보고서는 그대로여야 한다, §9)."""
    user = make_user()
    call_count = {"n": 0}

    def fake_snapshot(db, u, period_from, period_to, providers):
        call_count["n"] += 1
        return {"summary": {"call": call_count["n"]}}

    monkeypatch.setattr(reports_router, "build_cost_snapshot", fake_snapshot)

    created = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user)).json()["data"]
    assert call_count["n"] == 1
    assert created["cost_snapshot"] == {"summary": {"call": 1}}

    fetched = client.get(f"/api/v1/reports/{created['id']}", headers=auth_header(user)).json()["data"]
    assert call_count["n"] == 1  # GET은 재계산하지 않는다
    assert fetched["cost_snapshot"] == {"summary": {"call": 1}}  # 저장된 값 그대로


def test_recreating_same_conditions_refreshes_cost_snapshot(client, make_user, auth_header, monkeypatch):
    """같은 조건으로 다시 "생성하기"를 누르면(=명시적 재생성 요청) 비용도 다시 계산해 갱신한다
    — ON CONFLICT DO UPDATE가 generated_at과 함께 cost_snapshot도 새로 쓴다."""
    user = make_user()
    call_count = {"n": 0}

    def fake_snapshot(db, u, period_from, period_to, providers):
        call_count["n"] += 1
        return {"summary": {"call": call_count["n"]}}

    monkeypatch.setattr(reports_router, "build_cost_snapshot", fake_snapshot)

    first = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user)).json()["data"]
    second = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user)).json()["data"]

    assert first["id"] == second["id"]
    assert call_count["n"] == 2
    assert second["cost_snapshot"] == {"summary": {"call": 2}}


def test_cost_snapshot_failure_does_not_fail_report_creation(client, make_user, auth_header, monkeypatch):
    """비용 계산이 예외를 던져도 보고서 생성 자체는 성공해야 한다(§9 — 비용 섹션만 실패로
    남기고 나머지를 막지 않는다). cost_snapshot은 None으로 저장된다."""
    user = make_user()

    def failing_snapshot(db, u, period_from, period_to, providers):
        raise RuntimeError("cost boom")

    monkeypatch.setattr(reports_router, "build_cost_snapshot", failing_snapshot)

    resp = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["cost_snapshot"] is None


def test_cost_snapshot_uses_real_cost_query_functions_end_to_end(client, make_user, auth_header, db_session):
    """monkeypatch 없이 실제 app/cost/query.py 계산 결과가 스냅샷에 그대로 들어가는지 확인한다
    — 보고서가 비용 파트의 공통 함수를 우회하지 않고 재사용한다는 계약의 핵심 검증."""
    user = make_user()
    account = _make_account(db_session, user)
    _add_cost_row(db_session, account, dt.date(2026, 9, 15), "100.00")
    _add_cost_row(db_session, account, dt.date(2026, 9, 16), "50.00")

    payload = dict(_PAYLOAD, clouds=["aws"])
    resp = client.post("/api/v1/reports", json=payload, headers=auth_header(user))
    assert resp.status_code == 200
    snapshot = resp.json()["data"]["cost_snapshot"]

    assert snapshot is not None
    # period.end는 exclusive 변환 결과(period_to=09-19 → 09-20)까지 확인한다.
    assert snapshot["summary"]["period"] == {
        "start": "2026-09-13", "end": "2026-09-20", "display": "2026-09-13 ~ 2026-09-19",
    }
    mtd_actual = snapshot["summary"]["kpis"]["mtd_actual"]
    assert len(mtd_actual) == 1
    assert mtd_actual[0]["currency"] == "USD"
    assert mtd_actual[0]["amount"] == "150.000000"

    assert snapshot["breakdown_provider"]["items"] == [
        {"key": "aws", "label": "aws", "amount": "150.000000", "share_pct": "100.0"}
    ]


def test_cost_snapshot_category_uses_real_aws_service_names(client, make_user, auth_header, db_session):
    """cost/query.py::_AWS_SERVICE_TO_CATEGORY는 "AmazonEC2" 같은 짧은 코드를 키로 쓰는데
    실제 AWS Cost Explorer는 "Amazon Elastic Compute Cloud - Compute" 같은 정식 명칭을 준다
    (2026-09-21 실 화면에서 재현·확인). 그 버그 자체는 이승현 소유 파일(cost/query.py)이라
    고치지 않았고, 대신 app/report_cost.py::_category_breakdown_from_service()가 자체적으로
    실제 서비스명을 매핑한다 — 이 테스트는 그 매핑이 실제로 동작하는지, 그리고 cost/query.py
    쪽 코드는 전혀 안 건드렸는지(다른 화면엔 영향 없음)를 함께 확인한다."""
    user = make_user()
    account = _make_account(db_session, user)
    _add_cost_row(db_session, account, dt.date(2026, 9, 15), "40.00", service="Amazon Elastic Compute Cloud - Compute")
    _add_cost_row(db_session, account, dt.date(2026, 9, 16), "5.00", service="EC2 - Other")
    _add_cost_row(db_session, account, dt.date(2026, 9, 17), "10.00", service="Amazon Simple Storage Service")
    _add_cost_row(db_session, account, dt.date(2026, 9, 18), "3.00", service="Amazon Virtual Private Cloud")  # 매핑표에 없는 서비스

    payload = dict(_PAYLOAD, clouds=["aws"])
    resp = client.post("/api/v1/reports", json=payload, headers=auth_header(user))
    assert resp.status_code == 200
    category = resp.json()["data"]["cost_snapshot"]["breakdown_category"]

    by_key = {item["key"]: item["amount"] for item in category["items"]}
    assert by_key == {"compute": "45.000000", "storage_object": "10.000000"}
    # 매핑표에 없는 서비스(VPC)는 지어내지 않고 그대로 미분류로 남는다.
    assert category["unallocated"]["amount"] == "3.000000"
    assert category["unallocated"]["reason"] == "no_category_mapping"
    assert category["total"] == "58.000000"

    # 2026-09-23(이승현, 5단계): cost/query.py::_AWS_SERVICE_TO_CATEGORY에 CE 정식 명칭 키를
    # 추가해 원래 버그를 고쳤다. 그래서 "비용 쪽은 여전히 전액 미분류"라는 예전 단언(=버그가
    # 남아 있다는 증거)은 더 이상 참이 아니다 — 이제 두 경로가 **같은 결과**를 낸다는 것으로
    # 바꾼다(보고서 우회표와 비용 매핑표의 값이 어긋나지 않는지 지키는 역할은 그대로).
    # 두 표를 하나로 합치는 것은 소유가 갈려 있어 후속 과제(6단계 "단일 소스 통합 검토").
    from app.cost.query import CostQuery, breakdown

    q = CostQuery(period_start=dt.date(2026, 9, 13), period_end=dt.date(2026, 9, 20), providers=["aws"])
    via_query = breakdown(db_session, user.id, q, "category", 6, None)
    assert {item["key"]: item["amount"] for item in via_query["items"]} == by_key
    assert via_query["unallocated"]["amount"] == "3.000000"
    assert via_query["unallocated"]["reason"] == "no_category_mapping"
