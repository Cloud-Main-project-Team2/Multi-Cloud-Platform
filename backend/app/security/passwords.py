"""bcrypt 비밀번호 해시 검증. app.seed_mock_data가 이미 bcrypt로 해시를 만들어 두므로
알고리즘을 그대로 재사용한다(새 해시 방식을 도입하지 않는다).
"""

from __future__ import annotations

import bcrypt


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
