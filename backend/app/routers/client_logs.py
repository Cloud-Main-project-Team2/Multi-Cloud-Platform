"""프론트엔드 오류 수집 엔드포인트 (`POST /api/v1/client-logs`).

서버 로그만으로는 "화면이 안 떠요"의 원인을 볼 수 없어서, 브라우저에서 터진 JS 예외와 실패한
API 응답을 받아 `app.log`에 `client.error`로 남긴다. 설계 원칙 셋:

1. **에러만 받는다** — 사용자 행동 로그는 수집하지 않는다.
2. **수집 실패가 화면을 깨지 않는다** — 인증 없이도 받고(로그인 전 에러도 봐야 한다), 한도를
   넘으면 429 대신 조용히 버리고 204로 답한다. 429를 주면 그 자체가 `http.api_error` 로그를
   만들어 로그 폭탄이 배가된다.
3. **브라우저가 보낸 값은 신뢰하지 않는다** — 스키마에서 길이·건수를 자르고, redaction은
   `logging_config`가 한 번 더 한다.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Header, Request, Response, status

from app.logging_config import log_business_event
from app.schemas.client_logs import ClientLogBatch
from app.security.jwt_tokens import TokenError, decode_user_id

router = APIRouter(prefix="/api/v1", tags=["client-logs"])

# IP당 창(60초) 안에서 받을 수 있는 최대 엔트리 수. 단일 프로세스 전제는 sync-jobs의
# `_CANCEL_REQUESTED`와 같은 종류의 제약이다(replica를 늘리면 IP당 한도가 replica 배가 된다).
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_ENTRIES = 60
_rate_state: dict[str, tuple[float, int]] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str, count: int) -> bool:
    """창 안에서 한도를 넘으면 True. 한도를 넘는 순간 한 줄만 남기고 이후는 조용히 버린다."""
    now = time.monotonic()
    window_started, used = _rate_state.get(ip, (now, 0))
    if now - window_started >= _RATE_WINDOW_SECONDS:
        window_started, used = now, 0

    if used >= _RATE_MAX_ENTRIES:
        _rate_state[ip] = (window_started, used + count)
        return True

    _rate_state[ip] = (window_started, used + count)
    if used + count > _RATE_MAX_ENTRIES:
        log_business_event("client.rate_limited", level="WARNING", client_ip=ip, window_seconds=_RATE_WINDOW_SECONDS)
    return False


def _optional_user_id(authorization: str | None) -> int | None:
    """로그인 상태면 user_id를 붙인다. 토큰이 없거나 틀려도 401을 내지 않는다(에러는 받아야 한다)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        return decode_user_id(authorization.split(" ", 1)[1].strip())
    except TokenError:
        return None


@router.post("/client-logs", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def collect_client_logs(
    payload: ClientLogBatch,
    request: Request,
    authorization: str | None = Header(default=None),
    user_agent: str | None = Header(default=None),
) -> Response:
    ip = _client_ip(request)
    if not _rate_limited(ip, len(payload.entries)):
        user_id = _optional_user_id(authorization)
        for entry in payload.entries:
            log_business_event(
                "client.error",
                level="ERROR",
                user_id=user_id,
                client_ip=ip,
                user_agent=(user_agent or "")[:300] or None,
                **entry.model_dump(exclude_none=True),
            )
    # 한도를 넘었든 아니든 204 — 리포터가 실패를 다시 리포트하는 루프를 막는다.
    return Response(status_code=status.HTTP_204_NO_CONTENT)
