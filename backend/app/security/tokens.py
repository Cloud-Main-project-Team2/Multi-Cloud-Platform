"""토큰/코드 생성·해시 공용 유틸.

이메일 검증(OTP 코드), 비밀번호 재설정(링크 토큰), refresh token 세 곳이 공통으로 쓴다.
DB에는 평문이 아니라 SHA-256 해시만 저장하고(기존 `password_reset_tokens.token_hash` 규약과
동일), 대조할 때는 상수 시간 비교(`hmac.compare_digest`)를 쓴다.

- opaque 토큰(비밀번호 재설정·refresh)은 `secrets.token_urlsafe`로 충분한 엔트로피를 가지므로
  SHA-256 단방향 해시로 저장한다(bcrypt까지는 불필요).
- OTP 코드는 6자리라 엔트로피가 낮지만, 만료(10분)+시도 횟수 제한(5회)으로 무차별 대입을 막는다.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_token() -> str:
    """비밀번호 재설정·refresh token용 고엔트로피 opaque 토큰(URL-safe)."""
    return secrets.token_urlsafe(32)


def generate_numeric_code(digits: int = 6) -> str:
    """이메일 검증용 숫자 OTP 코드. 앞자리 0을 허용하려 문자열로 zero-pad 한다."""
    upper = 10**digits
    return f"{secrets.randbelow(upper):0{digits}d}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)
