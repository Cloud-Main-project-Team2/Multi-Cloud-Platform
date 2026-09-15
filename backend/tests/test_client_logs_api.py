"""프론트엔드 오류 수집(`POST /api/v1/client-logs`) — 수집이 화면을 깨지 않고, 브라우저가 보낸
값이 로그를 오염시키지 못하는지 고정한다.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager

import pytest

from app.logging_config import app_logger
from app.routers import client_logs as client_logs_module


@contextmanager
def capture():
    lines: list[dict] = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(json.loads(record.getMessage()))
    app_logger.addHandler(handler)
    try:
        yield lines
    finally:
        app_logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    client_logs_module._rate_state.clear()
    yield
    client_logs_module._rate_state.clear()


def _entry(**overrides) -> dict:
    entry = {"kind": "js_error", "message": "TypeError: x is not a function"}
    entry.update(overrides)
    return entry


def test_collects_without_authentication(client):
    """로그인 전(로그인 화면)에서 난 에러도 받아야 하므로 인증을 요구하지 않는다."""
    with capture() as lines:
        response = client.post("/api/v1/client-logs", json={"entries": [_entry()]})

    assert response.status_code == 204
    logged = [l for l in lines if l["event"] == "client.error"]
    assert len(logged) == 1
    assert logged[0]["level"] == "ERROR"
    assert logged[0]["kind"] == "js_error"
    assert logged[0]["user_id"] is None


def test_api_error_keeps_server_request_id(client):
    """프론트에서 본 실패 한 건을 백엔드 로그의 같은 request_id 줄과 잇는다."""
    with capture() as lines:
        client.post(
            "/api/v1/client-logs",
            json={
                "entries": [
                    _entry(
                        kind="api_error",
                        message="서버 오류",
                        api_path="/resources",
                        api_status=500,
                        server_request_id="req-abc",
                    )
                ]
            },
        )

    logged = [l for l in lines if l["event"] == "client.error"][-1]
    assert logged["api_path"] == "/resources"
    assert logged["api_status"] == 500
    assert logged["server_request_id"] == "req-abc"


def test_authenticated_report_carries_user_id(client, make_user, auth_header):
    user = make_user(email="reporter@example.com")
    with capture() as lines:
        client.post("/api/v1/client-logs", json={"entries": [_entry()]}, headers=auth_header(user))

    assert [l for l in lines if l["event"] == "client.error"][-1]["user_id"] == user.id


def test_invalid_token_is_accepted_as_anonymous(client):
    """토큰이 썩었다고 에러 수집까지 막으면 정작 그 토큰 문제를 볼 수 없다."""
    with capture() as lines:
        response = client.post(
            "/api/v1/client-logs",
            json={"entries": [_entry()]},
            headers={"Authorization": "Bearer not-a-real-token"},
        )

    assert response.status_code == 204
    assert [l for l in lines if l["event"] == "client.error"][-1]["user_id"] is None


def test_oversized_payload_is_rejected(client):
    too_many = client.post("/api/v1/client-logs", json={"entries": [_entry()] * 11})
    too_long = client.post("/api/v1/client-logs", json={"entries": [_entry(message="x" * 501)]})
    unknown_kind = client.post("/api/v1/client-logs", json={"entries": [_entry(kind="page_view")]})

    assert (too_many.status_code, too_long.status_code, unknown_kind.status_code) == (422, 422, 422)


def test_rate_limit_drops_silently(client):
    """한도를 넘어도 429가 아니라 204 — 429는 그 자체로 http.api_error 로그를 만들어
    로그 폭탄을 배가시킨다."""
    batch = {"entries": [_entry(message=f"error-{i}") for i in range(10)]}
    for _ in range(client_logs_module._RATE_MAX_ENTRIES // 10):
        client.post("/api/v1/client-logs", json=batch)

    with capture() as lines:
        response = client.post("/api/v1/client-logs", json=batch)

    assert response.status_code == 204
    assert [l for l in lines if l["event"] == "client.error"] == []
