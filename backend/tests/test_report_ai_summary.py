"""POST /reports의 AI 분석 요약(`ai_summary`) 저장 계약 검증(2026-09-23).

`cost_snapshot`과 같은 패턴이다 — `app.routers.reports.build_ai_summary`를 monkeypatch해서
실제 OpenAI 호출 없이 "생성 시점에 고정 저장되고 조회 시 재계산하지 않는다"는 계약만
확인한다(LLM 호출 자체의 검증은 tests/test_agent.py가 `call_chat_completion` 경로로 담당).
"""

from __future__ import annotations

import app.routers.reports as reports_router
from app.models import ReportGeneration

_PAYLOAD = {
    "period_type": "WEEKLY",
    "period_from": "2026-09-13",
    "period_to": "2026-09-19",
    "clouds": ["aws", "azure", "gcp"],
}


def test_ai_summary_is_stored(client, make_user, auth_header, monkeypatch):
    user = make_user()

    async def fake_summary(db, u, cost_snapshot):
        return {"paragraph": "요약 문단", "actions": ["조치 1"]}

    monkeypatch.setattr(reports_router, "build_ai_summary", fake_summary)

    resp = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["ai_summary"] == {"paragraph": "요약 문단", "actions": ["조치 1"]}

    row = client.get(
        f"/api/v1/reports/{resp.json()['data']['id']}", headers=auth_header(user)
    ).json()["data"]
    assert row["ai_summary"] == {"paragraph": "요약 문단", "actions": ["조치 1"]}


def test_ai_summary_receives_the_cost_snapshot(client, make_user, auth_header, monkeypatch):
    """비용과 사용률/미사용 리소스를 근거로 요약을 만들어야 하므로, 방금 계산한 cost_snapshot을
    그대로 전달받는지 확인한다(재계산하지 않고 인자로 받은 값을 그대로 인용해야 함, §1)."""
    user = make_user()
    monkeypatch.setattr(
        reports_router, "build_cost_snapshot",
        lambda db, u, period_from, period_to, providers: {"summary": {"fake": True}},
    )
    seen = {}

    async def fake_summary(db, u, cost_snapshot):
        seen["cost_snapshot"] = cost_snapshot
        return {"paragraph": "p", "actions": []}

    monkeypatch.setattr(reports_router, "build_ai_summary", fake_summary)

    client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    assert seen["cost_snapshot"] == {"summary": {"fake": True}}


def test_reopening_report_does_not_recompute_ai_summary(client, make_user, auth_header, monkeypatch):
    """생성 시점 값이 고정돼야 한다 — GET으로 다시 열어도 build_ai_summary를 또 부르면 안 된다
    (cost_snapshot과 동일 정책, §9)."""
    user = make_user()
    call_count = {"n": 0}

    async def fake_summary(db, u, cost_snapshot):
        call_count["n"] += 1
        return {"paragraph": f"call {call_count['n']}", "actions": []}

    monkeypatch.setattr(reports_router, "build_ai_summary", fake_summary)

    created = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user)).json()["data"]
    assert call_count["n"] == 1

    fetched = client.get(f"/api/v1/reports/{created['id']}", headers=auth_header(user)).json()["data"]
    assert call_count["n"] == 1  # GET은 재계산하지 않는다
    assert fetched["ai_summary"] == {"paragraph": "call 1", "actions": []}


def test_recreating_same_conditions_refreshes_ai_summary(client, make_user, auth_header, monkeypatch):
    """같은 조건으로 다시 "생성하기"를 누르면 AI 요약도 다시 만들어 갱신한다 — ON CONFLICT DO
    UPDATE가 generated_at·cost_snapshot과 함께 ai_summary도 새로 쓴다."""
    user = make_user()
    call_count = {"n": 0}

    async def fake_summary(db, u, cost_snapshot):
        call_count["n"] += 1
        return {"paragraph": f"call {call_count['n']}", "actions": []}

    monkeypatch.setattr(reports_router, "build_ai_summary", fake_summary)

    first = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user)).json()["data"]
    second = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user)).json()["data"]

    assert first["id"] == second["id"]
    assert call_count["n"] == 2
    assert second["ai_summary"] == {"paragraph": "call 2", "actions": []}


def test_ai_summary_failure_does_not_fail_report_creation(client, make_user, auth_header, monkeypatch):
    """OPENAI_API_KEY 미설정/LLM 오류/응답 파싱 실패 등 어떤 이유로 실패하든 보고서 생성
    자체는 성공해야 한다(§9 — AI 요약 섹션만 실패로 남기고 나머지를 막지 않는다).
    ai_summary는 None으로 저장된다."""
    user = make_user()

    async def failing_summary(db, u, cost_snapshot):
        raise RuntimeError("llm boom")

    monkeypatch.setattr(reports_router, "build_ai_summary", failing_summary)

    resp = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["ai_summary"] is None


def test_ai_summary_defaults_to_none_when_never_computed(client, make_user, auth_header, db_session):
    """마이그레이션 직후 옛 행처럼 ai_summary가 아예 없는 경우에도 응답이 깨지지 않아야 한다."""
    user = make_user()
    resp = client.post("/api/v1/reports", json=_PAYLOAD, headers=auth_header(user))
    row = db_session.query(ReportGeneration).filter_by(user_id=user.id).one()
    row.ai_summary = None
    db_session.commit()

    fetched = client.get(f"/api/v1/reports/{resp.json()['data']['id']}", headers=auth_header(user))
    assert fetched.json()["data"]["ai_summary"] is None
