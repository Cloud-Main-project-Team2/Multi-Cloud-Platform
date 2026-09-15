"""`POST /agent/chat` API 계약 검증. LLM 호출(`ask_agent`)은 monkeypatch로 대체한다 —
실제 네트워크/Anthropic API를 부르지 않는다."""

from __future__ import annotations

import app.routers.agent as agent_router
from app.agent import AgentNotConfiguredError, AgentUpstreamError


def test_agent_chat_requires_auth(client):
    resp = client.post("/api/v1/agent/chat", json={"message": "안녕"})
    assert resp.status_code == 401


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


def test_agent_chat_rejects_empty_message(client, make_user, auth_header):
    user = make_user()
    resp = client.post("/api/v1/agent/chat", json={"message": ""}, headers=auth_header(user))
    assert resp.status_code == 422


def test_agent_chat_returns_503_when_not_configured(client, make_user, auth_header, monkeypatch):
    user = make_user()

    async def fake_ask_agent(message, history, context):
        raise AgentNotConfiguredError()

    monkeypatch.setattr(agent_router, "ask_agent", fake_ask_agent)

    resp = client.post("/api/v1/agent/chat", json={"message": "안녕"}, headers=auth_header(user))

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "AGENT_NOT_CONFIGURED"


def test_agent_chat_returns_502_on_upstream_error(client, make_user, auth_header, monkeypatch):
    user = make_user()

    async def fake_ask_agent(message, history, context):
        raise AgentUpstreamError(500, "boom")

    monkeypatch.setattr(agent_router, "ask_agent", fake_ask_agent)

    resp = client.post("/api/v1/agent/chat", json={"message": "안녕"}, headers=auth_header(user))

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "AGENT_UPSTREAM_ERROR"
