"""`app/report_summary.py` 단위 검증 — 컨텍스트 조립 + OpenAI 호출(httpx는 monkeypatch).

`tests/test_agent.py`와 같은 스타일이다 — `build_ai_summary()`는 async def지만 테스트는
`asyncio.run()`으로 동기 테스트 함수 안에서 그냥 실행한다.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.agent import AgentNotConfiguredError, AgentUpstreamError
from app.report_summary import ReportSummaryParseError, build_ai_summary


def _fake_settings():
    return type("S", (), {"openai_api_key": "sk-test", "openai_model": "gpt-4o-mini"})()


def _mock_openai_json(monkeypatch, body: dict, status: int = 200):
    from app import agent as agent_module

    monkeypatch.setattr(agent_module, "get_settings", _fake_settings)

    async def fake_post(self, *args, **kwargs):
        request = httpx.Request("POST", agent_module._OPENAI_API_URL)
        return httpx.Response(status, json=body, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def test_build_ai_summary_returns_parsed_paragraph_and_actions(db_session, make_user, monkeypatch):
    user = make_user()
    content = json.dumps({"paragraph": "이번 주 비용이 늘었습니다.", "actions": ["A", "B"]}, ensure_ascii=False)
    _mock_openai_json(monkeypatch, {"choices": [{"message": {"content": content}}]})

    result = asyncio.run(build_ai_summary(db_session, user, {"summary": {"fake": True}}))
    assert result == {"paragraph": "이번 주 비용이 늘었습니다.", "actions": ["A", "B"]}


def test_build_ai_summary_caps_actions_and_drops_blank_entries(db_session, make_user, monkeypatch):
    user = make_user()
    content = json.dumps({"paragraph": "p", "actions": ["1", "", "2", "3", "4", "5"]})
    _mock_openai_json(monkeypatch, {"choices": [{"message": {"content": content}}]})

    result = asyncio.run(build_ai_summary(db_session, user, None))
    assert result["actions"] == ["1", "2", "3", "4"]  # 최대 4개, 빈 문자열 제외


def test_build_ai_summary_raises_on_malformed_json(db_session, make_user, monkeypatch):
    user = make_user()
    _mock_openai_json(monkeypatch, {"choices": [{"message": {"content": "이건 JSON이 아님"}}]})

    with pytest.raises(ReportSummaryParseError):
        asyncio.run(build_ai_summary(db_session, user, None))


def test_build_ai_summary_raises_on_empty_paragraph(db_session, make_user, monkeypatch):
    user = make_user()
    content = json.dumps({"paragraph": "   ", "actions": []})
    _mock_openai_json(monkeypatch, {"choices": [{"message": {"content": content}}]})

    with pytest.raises(ReportSummaryParseError):
        asyncio.run(build_ai_summary(db_session, user, None))


def test_build_ai_summary_raises_when_not_configured(db_session, make_user, monkeypatch):
    from app import agent as agent_module

    user = make_user()
    monkeypatch.setattr(agent_module, "get_settings", lambda: type("S", (), {"openai_api_key": ""})())

    with pytest.raises(AgentNotConfiguredError):
        asyncio.run(build_ai_summary(db_session, user, None))


def test_build_ai_summary_raises_upstream_error_on_non_200(db_session, make_user, monkeypatch):
    _mock_openai_json(monkeypatch, {}, status=500)
    user = make_user()

    with pytest.raises(AgentUpstreamError):
        asyncio.run(build_ai_summary(db_session, user, None))
