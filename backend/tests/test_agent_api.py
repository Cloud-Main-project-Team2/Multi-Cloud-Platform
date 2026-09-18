"""`POST /agent/chat` API 계약 검증. LLM 호출(`ask_agent`)은 monkeypatch로 대체한다 —
실제 네트워크/Anthropic API를 부르지 않는다.

2026-09-18: `app/routers/agent.py`가 "임시 차단"(보고서 모니터링 API 테스트 중 실수로 OpenAI
호출이 나가지 않도록) 코드로 `ask_agent` 호출 자체를 건너뛰고 항상 503
AGENT_TEMPORARILY_DISABLED를 반환하도록 바뀌었다 — `ask_agent`를 monkeypatch해서 정상 동작을
검증하는 아래 4개는 그 차단에 막혀 항상 실패한다. 라우터의 그 차단은 팀원이 의도적으로 넣어둔
것이라 테스트에서 우회하지 않고 skip한다 — 차단이 풀리면(agent.py의 그 블록이 지워지면) 이
skip도 같이 지운다."""

from __future__ import annotations

import pytest

import app.routers.agent as agent_router
from app.agent import AgentNotConfiguredError, AgentUpstreamError

_AGENT_TEMPORARILY_DISABLED = pytest.mark.skip(
    reason="app/routers/agent.py가 임시로 AI 호출을 전부 막아 뒀다(2026-09-18) — 차단이 풀리면 skip 제거"
)


def test_agent_chat_requires_auth(client):
    resp = client.post("/api/v1/agent/chat", json={"message": "안녕"})
    assert resp.status_code == 401


@_AGENT_TEMPORARILY_DISABLED
def test_agent_chat_returns_reply(client, make_user, auth_header, monkeypatch):
    user = make_user()

    async def fake_ask_agent(message, history, context):
        assert message == "비용 줄이는 방법 알려줘"
        assert history == []
        return "정가 기준 추정치로는 ..."

    monkeypatch.setattr(agent_router, "ask_agent", fake_ask_agent)

    resp = client.post(
        "/api/v1/agent/chat",
        json={"message": "비용 줄이는 방법 알려줘"},
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["reply"] == "정가 기준 추정치로는 ..."


@_AGENT_TEMPORARILY_DISABLED
def test_agent_chat_passes_history_through(client, make_user, auth_header, monkeypatch):
    user = make_user()
    captured = {}

    async def fake_ask_agent(message, history, context):
        captured["history"] = history
        return "ok"

    monkeypatch.setattr(agent_router, "ask_agent", fake_ask_agent)

    resp = client.post(
        "/api/v1/agent/chat",
        json={
            "message": "그럼 GCP는?",
            "history": [{"role": "user", "content": "AWS 비용 알려줘"}, {"role": "assistant", "content": "..."}],
        },
        headers=auth_header(user),
    )

    assert resp.status_code == 200
    assert captured["history"] == [
        {"role": "user", "content": "AWS 비용 알려줘"},
        {"role": "assistant", "content": "..."},
    ]


def test_agent_chat_is_temporarily_disabled(client, make_user, auth_header, monkeypatch):
    # 위 skip 처리된 4개가 검증하던 "정상 동작"은 지금 이 차단 때문에 확인할 수 없다 — 대신
    # 지금 실제로 켜져 있는 동작(차단) 자체를 검증해 커버리지 공백을 안 남긴다.
    user = make_user()
    called = []
    monkeypatch.setattr(agent_router, "ask_agent", lambda *a, **k: called.append(1))

    resp = client.post("/api/v1/agent/chat", json={"message": "안녕"}, headers=auth_header(user))

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "AGENT_TEMPORARILY_DISABLED"
    assert called == []  # ask_agent까지 도달하지 않아야 한다(OpenAI 호출 방지가 목적)


def test_agent_chat_rejects_empty_message(client, make_user, auth_header):
    user = make_user()
    resp = client.post("/api/v1/agent/chat", json={"message": ""}, headers=auth_header(user))
    assert resp.status_code == 422


@_AGENT_TEMPORARILY_DISABLED
def test_agent_chat_returns_503_when_not_configured(client, make_user, auth_header, monkeypatch):
    user = make_user()

    async def fake_ask_agent(message, history, context):
        raise AgentNotConfiguredError()

    monkeypatch.setattr(agent_router, "ask_agent", fake_ask_agent)

    resp = client.post("/api/v1/agent/chat", json={"message": "안녕"}, headers=auth_header(user))

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "AGENT_NOT_CONFIGURED"


@_AGENT_TEMPORARILY_DISABLED
def test_agent_chat_returns_502_on_upstream_error(client, make_user, auth_header, monkeypatch):
    user = make_user()

    async def fake_ask_agent(message, history, context):
        raise AgentUpstreamError(500, "boom")

    monkeypatch.setattr(agent_router, "ask_agent", fake_ask_agent)

    resp = client.post("/api/v1/agent/chat", json={"message": "안녕"}, headers=auth_header(user))

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "AGENT_UPSTREAM_ERROR"
