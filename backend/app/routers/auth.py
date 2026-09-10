"""API 명세서 v1.1 §5 중 이 세션 범위: `POST /auth/login`만 구현한다.

회원가입·비밀번호 재설정·`/me`는 범위 밖(CLAUDE.md 결정 기록 참고) — credentials API가
동작하려면 최소한 로그인으로 access token을 발급받을 수 있어야 하므로 이 엔드포인트만 추가한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError
from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, LoginResponseData, UserOut
from app.security.jwt_tokens import create_access_token
from app.security.passwords import verify_password
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _normalize_email(email: str) -> str:
    return email.strip().casefold()


def _serialize_user(user: User) -> UserOut:
    return UserOut(
        id=str_id(user.id),
        email=user.email,
        name=user.name,
        affiliation_type=user.affiliation_type,
        affiliation_name=user.affiliation_name,
        status=user.status,
        created_at=iso_z(user.created_at),
        updated_at=iso_z(user.updated_at),
        withdrawn_at=iso_z(user.withdrawn_at),
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    normalized_email = _normalize_email(payload.email)
    user = db.query(User).filter_by(normalized_email=normalized_email).one_or_none()

    if user is None or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise ApiError(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.status == "withdrawn":
        raise ApiError(401, "USER_WITHDRAWN", "탈퇴한 계정입니다.")

    access_token, expires_in = create_access_token(user.id)
    return LoginResponse(
        data=LoginResponseData(
            access_token=access_token,
            expires_in=expires_in,
            user=_serialize_user(user),
        )
    )
