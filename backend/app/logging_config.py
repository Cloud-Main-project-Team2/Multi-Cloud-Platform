"""API 명세서 v1.1 §18 로깅 요구사항: access.log/app.log 분리 + secret redaction.

- access.log: request_id, method, path template, status, duration, user_id(가능하면). Authorization/
  Cookie/request body는 절대 기록하지 않는다(애초에 만들어 넘기지 않는다).
- app.log: 비즈니스 상태 전이·CSP 호출 결과. secret 값 자체를 넘기지 않는 것이 1차 방어이고,
  아래 `_redact`는 실수로라도 새는 것을 막는 2차 방어(defense in depth)다.
"""

from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

LOG_DIR = os.environ.get("LOG_DIR", "logs")

_REDACT_KEYS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "secret_payload",
    "secret_access_key",
    "access_key_id",
    "client_secret",
    "private_key",
    "private_key_id",
    "public_identifier",
    "token",
    "access_token",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***" if k.lower() in _REDACT_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _make_logger(name: str, filename: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    os.makedirs(LOG_DIR, exist_ok=True)
    handler = RotatingFileHandler(os.path.join(LOG_DIR, filename), maxBytes=5_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


access_logger = _make_logger("mcp.access", "access.log")
app_logger = _make_logger("mcp.app", "app.log")


def log_access(**fields: Any) -> None:
    # alembic/env.py는 logging.config.fileConfig()를 기본값(disable_existing_loggers=True)으로
    # 호출한다 — 마이그레이션이 실행되는 시점 이후 이 로거가 조용히 꺼지는 것을 막는다.
    access_logger.disabled = False
    access_logger.info(json.dumps(_redact(fields), ensure_ascii=False, default=str))


def log_business_event(event: str, **fields: Any) -> None:
    app_logger.disabled = False
    app_logger.info(json.dumps(_redact({"event": event, **fields}), ensure_ascii=False, default=str))
