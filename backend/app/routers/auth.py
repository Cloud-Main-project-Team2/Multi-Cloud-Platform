"""API 명세서 v1.1 §5 중 이 세션 범위: `POST /auth/login` + `POST /auth/sign-up`.

비밀번호 재설정·`/me`는 여전히 범위 밖(CLAUDE.md 결정 기록 참고) — 실제 메일 발송 없이
토큰만 발급하는 데모형으로 구현해야 해서 별도 세션으로 미룬다.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError, validation_error
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LoginResponseData,
    SignUpRequest,
    SignUpResponse,
    UserOut,
)
from app.security.jwt_tokens import create_access_token
from app.security.passwords import hash_password, is_strong_password, verify_password
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


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


@router.post("/sign-up", response_model=SignUpResponse, status_code=201)
def sign_up(payload: SignUpRequest, db: Session = Depends(get_db)) -> SignUpResponse:
    email = payload.email.strip()
    if not _EMAIL_RE.match(email):
        raise validation_error("이메일 형식이 올바르지 않습니다.", details=[{"field": "email", "reason": "invalid_format"}])
    if not is_strong_password(payload.password):
        raise validation_error(
            "비밀번호는 8자 이상이며 영문/숫자/기호 중 2종 이상을 포함해야 합니다.",
            details=[{"field": "password", "reason": "too_weak"}],
        )
    affiliation_name = (payload.affiliation_name or "").strip() or None
    if payload.affiliation_type == "company" and not affiliation_name:
        raise validation_error(
            "소속 회사명을 입력해 주세요.", details=[{"field": "affiliation_name", "reason": "required"}]
        )

    normalized_email = _normalize_email(email)
    if db.query(User).filter_by(normalized_email=normalized_email).one_or_none() is not None:
        raise ApiError(409, "EMAIL_ALREADY_EXISTS", "이미 사용 중인 이메일입니다.")

    user = User(
        email=email,
        normalized_email=normalized_email,
        password_hash=hash_password(payload.password),
        name=payload.name.strip(),
        affiliation_type=payload.affiliation_type,
        affiliation_name=affiliation_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "EMAIL_ALREADY_EXISTS", "이미 사용 중인 이메일입니다.") from exc
    db.refresh(user)

    return SignUpResponse(data=_serialize_user(user))
