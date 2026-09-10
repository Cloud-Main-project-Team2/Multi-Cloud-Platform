"""bcrypt 비밀번호 해시 검증. app.seed_mock_data가 이미 bcrypt로 해시를 만들어 두므로
알고리즘을 그대로 재사용한다(새 해시 방식을 도입하지 않는다).
"""

from __future__ import annotations

import re

import bcrypt


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def is_strong_password(plain_password: str) -> bool:
    """8자 이상 + 영문/숫자/기호 중 2종 이상 — frontend/assets/js/validate.js의
    MCVAL.isStrongPassword와 동일한 규칙(§19 "비밀번호 정책"을 이 규칙으로 확정)."""
    if len(plain_password) < 8:
        return False
    types = 0
    if re.search(r"[A-Za-z]", plain_password):
        types += 1
    if re.search(r"[0-9]", plain_password):
        types += 1
    if re.search(r"[^A-Za-z0-9]", plain_password):
        types += 1
    return types >= 2
