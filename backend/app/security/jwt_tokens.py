"""최소 JWT access token 발급/검증.

API 명세서 v1.1 §5는 회원가입·비밀번호 재설정·`/me`까지 포함한 전체 인증 스펙을 정의하지만,
이번 세션(`solcho/be-credentials-api`)은 credentials API가 동작하기 위한 최소 범위 —
`POST /auth/login` + Bearer JWT 검증 — 만 구현한다. 나머지 §5 엔드포인트는 범위 밖이며
CLAUDE.md에 결정을 기록한다.

refresh token은 다루지 않는다(§19 "refresh token 전달·회전·폐기 정책" 미확정 — access token만
발급하고 만료되면 재로그인하는 것으로 이번 세션은 충분하다고 본다).
"""

from __future__ import annotations

import time

import jwt

from app.config import get_settings


class TokenError(Exception):
    """서명 불일치, 만료, 형식 오류 등 access token을 신뢰할 수 없을 때 발생."""


def create_access_token(user_id: int) -> tuple[str, int]:
    settings = get_settings()
    now = int(time.time())
    expires_in = settings.jwt_access_token_expires_seconds
    payload = {"sub": str(user_id), "iat": now, "exp": now + expires_in}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_user_id(token: str) -> int:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError("invalid or expired token") from exc
    try:
        return int(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("invalid token subject") from exc
