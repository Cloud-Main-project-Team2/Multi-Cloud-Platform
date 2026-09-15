"""API 명세서 v1.1 §18 로깅 요구사항: access.log/app.log 분리 + secret redaction.

- access.log: request_id, method, path template, status, duration, user_id(가능하면). Authorization/
  Cookie/request body는 절대 기록하지 않는다(애초에 만들어 넘기지 않는다).
- app.log: 비즈니스 상태 전이·CSP 호출 결과. secret 값 자체를 넘기지 않는 것이 1차 방어이고,
  아래 `_redact`는 실수로라도 새는 것을 막는 2차 방어(defense in depth)다.

두 파일 모두 **JSON Lines**다. 사람이 tail로 훑는 것보다 `jq`로 거르는 쪽이 관제에 쓸모 있고,
redaction이 "키 이름" 기준이라 구조화된 형태를 유지해야 하기 때문이다. 모든 라인은
`ts` / `level` / `logger`로 시작한다 — 시각 없는 로그는 관제에 쓸 수 없다.

`ts`는 **한국 시간(+09:00)**이다. 로그는 사람이 읽는 물건이고, 관제하는 사람이 서울에 있으니
UTC로 적어두면 매번 9시간을 암산해야 한다. 오프셋을 문자열에 그대로 남기므로 나중에 UTC나
다른 시간대와 비교해도 모호하지 않다. **API 응답·DB의 시각은 UTC 그대로다**(`serialization.iso_z`)
— 그쪽은 기계가 읽는 계약이라 건드리지 않는다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from typing import Any

LOG_DIR = os.environ.get("LOG_DIR", "logs")
# 10MB × 5 = 파일당 최대 약 50MB, 두 로거 합쳐 100MB 정도를 디스크 상한으로 본다.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


def _log_timezone() -> dt.timezone:
    """로그 타임스탬프의 시간대. 기본 KST(+09:00).

    `zoneinfo`가 아니라 고정 오프셋을 쓰는 이유: 한국은 서머타임이 없어 +09:00이 언제나 정확하고,
    slim 계열 이미지에 tzdata가 없어도 동작한다.
    """
    try:
        offset_hours = float(os.environ.get("LOG_TZ_OFFSET_HOURS", "9"))
    except ValueError:
        offset_hours = 9.0
    return dt.timezone(dt.timedelta(hours=offset_hours))


LOG_TZ = _log_timezone()
# 10MB × 5 = 파일당 최대 약 50MB, 두 로거 합쳐 100MB 정도를 디스크 상한으로 본다.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

_REDACT_KEYS = {
    "authorization",
    "cookie",
    "password",
    "admin_password",
    "master_password",
    "secret",
    "secret_payload",
    "secret_access_key",
    "access_key_id",
    "session_token",
    "client_secret",
    "private_key",
    "private_key_id",
    "public_identifier",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "openai_api_key",
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


def _now() -> str:
    # 예: 2026-09-15T22:27:40.917+09:00 — 오프셋을 남겨 다른 시간대와 비교해도 모호하지 않게 한다.
    return dt.datetime.now(LOG_TZ).isoformat(timespec="milliseconds")


def _handler(filename: str) -> logging.Handler:
    """파일 핸들러를 만들되, 로그 디렉터리를 쓸 수 없으면 stderr로 폴백한다.

    docker-compose가 `./logs`를 바인드 마운트하는데 호스트 디렉터리 소유자가 컨테이너 사용자
    (uid 1000 appuser)와 다르면 파일 생성이 실패한다. 그때 예외를 그대로 올리면 **로깅 때문에
    서비스 자체가 기동하지 못한다** — 로그는 보조 기능이므로 앱을 죽이지 않는다.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        return RotatingFileHandler(
            os.path.join(LOG_DIR, filename), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
        )
    except OSError as exc:  # pragma: no cover - 환경 의존
        print(f"[logging] {LOG_DIR}/{filename} 사용 불가({exc}) — stderr로 대체합니다.", file=sys.stderr)
        return logging.StreamHandler(sys.stderr)


def _make_logger(name: str, filename: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = _handler(filename)
    handler.setFormatter(logging.Formatter("%(message)s"))  # 라인 자체를 JSON으로 만들어 넘긴다
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


access_logger = _make_logger("mcp.access", "access.log")
app_logger = _make_logger("mcp.app", "app.log")

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


def _emit(logger: logging.Logger, level: str, payload: dict[str, Any]) -> None:
    # alembic/env.py는 logging.config.fileConfig()를 기본값(disable_existing_loggers=True)으로
    # 호출한다 — 마이그레이션이 실행되는 시점 이후 이 로거가 조용히 꺼지는 것을 막는다.
    logger.disabled = False
    line = {"ts": _now(), "level": level, "logger": logger.name.split(".")[-1], **_redact(payload)}
    logger.log(_LEVELS.get(level, logging.INFO), json.dumps(line, ensure_ascii=False, default=str))


def _level_for_status(status: Any) -> str:
    if isinstance(status, int):
        if status >= 500:
            return "ERROR"
        if status >= 400:
            return "WARNING"
    return "INFO"


def log_access(**fields: Any) -> None:
    """요청 1건 = 1줄. status로 level을 갈라 `jq 'select(.level=="ERROR")'`로 5xx만 뽑을 수 있게 한다."""
    _emit(access_logger, _level_for_status(fields.get("status")), fields)


def log_business_event(event: str, *, level: str = "INFO", exc_info: bool = False, **fields: Any) -> None:
    """비즈니스 이벤트 1건. 이름은 `<도메인>.<동작>` 규약을 따른다(`provisioning.job.failed` 등).

    `exc_info=True`는 현재 처리 중인 예외의 스택트레이스를 `exc` 필드에 담는다. 포맷터가
    `%(message)s`뿐이라 logging 기본 exc_info 출력을 쓰면 JSON 한 줄이 깨지므로 직접 넣는다.
    """
    payload: dict[str, Any] = {"event": event, **fields}
    if exc_info:
        payload["exc"] = traceback.format_exc()
    _emit(app_logger, level, payload)


@contextmanager
def log_background_task(task: str, **fields: Any) -> Iterator[None]:
    """백그라운드 태스크의 시작/종료/실패를 남긴다.

    `BackgroundTasks`로 도는 프로비저닝·동기화 job은 요청 사이클 밖이라 main.py의 전역 예외
    핸들러가 잡지 못한다 — 감싸지 않으면 여기서 터진 예외는 어디에도 남지 않는다. 예외는
    기록만 하고 **그대로 다시 올린다**(기존 동작을 바꾸지 않는다).
    """
    started_at = time.monotonic()
    log_business_event(f"{task}.started", **fields)
    try:
        yield
    except BaseException as exc:
        log_business_event(
            f"{task}.crashed",
            level="ERROR",
            exc_info=True,
            duration_ms=round((time.monotonic() - started_at) * 1000, 2),
            error_type=type(exc).__name__,
            **fields,
        )
        raise
    else:
        log_business_event(
            f"{task}.finished",
            duration_ms=round((time.monotonic() - started_at) * 1000, 2),
            **fields,
        )
