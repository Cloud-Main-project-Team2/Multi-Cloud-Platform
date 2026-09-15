"""로깅 회귀 고정: 관제에 필요한 세 가지가 실제로 남는지 검사한다.

1) 모든 로그 라인에 시각(`ts`)과 심각도(`level`)가 있다 — 없으면 관제에 쓸 수 없다.
2) 처리되지 않은 예외(500)가 스택트레이스와 함께 app.log에 남는다.
3) 백그라운드 태스크에서 터진 예외가 남는다 — 요청 사이클 밖이라 전역 핸들러가 못 잡는 구간.

파일이 아니라 로거에 임시 핸들러를 달아 검증해 테스트를 결정적으로 만든다(test_secret_redaction.py와 동일 방식).
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager

import pytest

from app.logging_config import access_logger, app_logger, log_background_task


@contextmanager
def capture(logger: logging.Logger):
    lines: list[dict] = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(json.loads(record.getMessage()))
    logger.addHandler(handler)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)


def test_access_line_has_timestamp_level_and_request_fields(client):
    with capture(access_logger) as lines:
        client.get("/health")

    line = lines[-1]
    assert line["ts"].endswith("Z")
    assert line["level"] == "INFO"
    assert line["logger"] == "access"
    assert (line["method"], line["path"], line["status"]) == ("GET", "/health", 200)
    assert isinstance(line["duration_ms"], float)
    assert "user_id" in line and "client_ip" in line


def test_unhandled_exception_is_logged_with_traceback(client):
    app = client.app

    @app.get("/__test_boom")
    def _boom():  # pragma: no cover - 예외를 내는 것이 목적
        raise RuntimeError("boom-for-logging-test")

    try:
        with capture(app_logger) as app_lines, capture(access_logger) as access_lines:
            response = client.get("/__test_boom")
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/__test_boom"]

    assert response.status_code == 500
    # 응답에는 내부 정보가 없어야 한다(§17).
    assert "boom-for-logging-test" not in response.text

    logged = [l for l in app_lines if l["event"] == "http.unhandled_exception"]
    assert len(logged) == 1
    assert logged[0]["level"] == "ERROR"
    assert logged[0]["error_type"] == "RuntimeError"
    assert "Traceback" in logged[0]["exc"] and "boom-for-logging-test" in logged[0]["exc"]
    assert logged[0]["request_id"]

    # 같은 요청이 access.log에도 500 / ERROR로 남아 request_id로 이어진다.
    assert access_lines[-1]["status"] == 500
    assert access_lines[-1]["level"] == "ERROR"
    assert access_lines[-1]["request_id"] == logged[0]["request_id"]


def test_api_error_records_error_code(client):
    with capture(app_logger) as lines:
        client.get("/api/v1/resources")  # 인증 헤더 없음 → ApiError(401)

    logged = [l for l in lines if l["event"] == "http.api_error"]
    assert logged and logged[-1]["status"] == 401
    assert logged[-1]["level"] == "WARNING"
    assert logged[-1]["error_code"]


def test_background_task_crash_is_logged_and_reraised():
    with capture(app_logger) as lines:
        with pytest.raises(ValueError):
            with log_background_task("provisioning.job", job_id=42):
                raise ValueError("background-boom")

    events = [l["event"] for l in lines]
    assert events == ["provisioning.job.started", "provisioning.job.crashed"]
    crash = lines[-1]
    assert crash["level"] == "ERROR"
    assert crash["job_id"] == 42
    assert crash["error_type"] == "ValueError"
    assert "background-boom" in crash["exc"]


def test_background_task_success_logs_start_and_finish():
    with capture(app_logger) as lines:
        with log_background_task("sync.job", job_id=7):
            pass

    assert [l["event"] for l in lines] == ["sync.job.started", "sync.job.finished"]
    assert isinstance(lines[-1]["duration_ms"], float)
