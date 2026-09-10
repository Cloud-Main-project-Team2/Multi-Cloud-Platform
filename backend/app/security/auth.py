"""요청 인증 dependency.

⚠️ TEMPORARY — 실제 인증(회원가입/로그인/JWT 발급, `docs/01_API_명세서_v1.1.md` 5절)이
아직 이 저장소에 구현되어 있지 않다(README "이번 단계에서 구현하지 않은 것" 참고).
그 작업이 들어오기 전까지, 소유권 검사가 필요한 다른 API(프로비저닝 등)를 막지 않도록
`Authorization: Bearer <user_id>` 형태만 파싱하는 자리표시자로 둔다.

실제 JWT 서명 검증으로 교체될 때 이 파일의 `get_current_user_id` 구현부만 바뀌면 되고,
이를 사용하는 라우터는 고치지 않아도 되도록 시그니처(입력: Authorization 헤더 / 출력:
int user_id)를 API 명세의 인증 계약과 동일하게 맞춰뒀다.
"""

from __future__ import annotations

from fastapi import Header

from app.errors import ApiError


def get_current_user_id(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "AUTHENTICATION_REQUIRED", "인증이 필요합니다.")

    token = authorization.removeprefix("Bearer ").strip()
    if not token.isdigit() or int(token) <= 0:
        raise ApiError(401, "INVALID_TOKEN", "유효하지 않은 인증 토큰입니다.")

    return int(token)
