"""API 명세서 v1.1 §5 인증 엔드포인트.

이번 세션(`solcho/be-auth-enhancements`)에서 다음을 추가했다:
- `GET /auth/me` — access token으로 현재 유저 조회
- `POST /auth/refresh` / `POST /auth/logout` — 회전하는 opaque refresh token
- 이메일 검증(OTP) — `POST /auth/email-verifications`(코드 발송) +
  `POST /auth/email-verifications/verify`(코드 확인) + 가입 게이트
- 비밀번호 재설정 — `POST /auth/password-reset`(링크 발송) +
  `POST /auth/password-reset/confirm`(토큰 검증·비밀번호 교체)

메일은 실제로 발송한다(`app/mailer.py`, 로컬은 MailHog). 기존 로그인/회원가입 본체는 유지하되,
로그인은 refresh token을, 회원가입은 이메일 검증 게이트를 추가로 적용한다.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.mailer import send_email
from app.models import EmailVerification, PasswordResetToken, RefreshToken, User
from app.schemas.auth import (
    EmailVerificationRequest,
    EmailVerificationRequestData,
    EmailVerificationRequestResponse,
    EmailVerifyData,
    EmailVerifyRequest,
    EmailVerifyResponse,
    LoginRequest,
    LoginResponse,
    LoginResponseData,
    LogoutRequest,
    MeResponse,
    MessageData,
    MessageResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshRequest,
    RefreshResponse,
    SignUpRequest,
    SignUpResponse,
    UserOut,
)
from app.security.jwt_tokens import create_access_token
from app.security.passwords import hash_password, is_strong_password, verify_password
from app.security.tokens import generate_numeric_code, generate_token, hash_token, verify_token
from app.serialization import iso_z, str_id

logger = logging.getLogger("app.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """DB에서 읽은 값이 naive면 UTC로 간주(테스트용 SQLite 대비)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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


def _issue_refresh_token(db: Session, user_id: int) -> tuple[str, int]:
    """새 refresh token을 발급하고 DB에 해시로 저장한다. (평문 토큰, 만료초) 반환."""
    settings = get_settings()
    token = generate_token()
    expires_in = settings.refresh_token_expires_seconds
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_token(token),
            expires_at=_now() + timedelta(seconds=expires_in),
        )
    )
    return token, expires_in


def _build_session_data(db: Session, user: User) -> LoginResponseData:
    access_token, expires_in = create_access_token(user.id)
    refresh_token, refresh_expires_in = _issue_refresh_token(db, user.id)
    return LoginResponseData(
        access_token=access_token,
        expires_in=expires_in,
        refresh_token=refresh_token,
        refresh_expires_in=refresh_expires_in,
        user=_serialize_user(user),
    )


# ---------------------------------------------------------------------------
# 로그인 / 로그아웃 / refresh / me
# ---------------------------------------------------------------------------
@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    normalized_email = _normalize_email(payload.email)
    user = db.query(User).filter_by(normalized_email=normalized_email).one_or_none()

    if user is None or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise ApiError(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.status == "withdrawn":
        raise ApiError(401, "USER_WITHDRAWN", "탈퇴한 계정입니다.")

    data = _build_session_data(db, user)
    db.commit()
    return LoginResponse(data=data)


@router.get("/me", response_model=MeResponse)
def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(data=_serialize_user(current_user))


@router.post("/refresh", response_model=RefreshResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> RefreshResponse:
    token_hash = hash_token(payload.refresh_token)
    row = db.query(RefreshToken).filter_by(token_hash=token_hash).one_or_none()

    if row is None or row.revoked_at is not None or _aware(row.expires_at) <= _now():
        raise ApiError(401, "INVALID_TOKEN", "유효하지 않은 refresh token입니다.")

    user = db.get(User, row.user_id)
    if user is None or user.status == "withdrawn":
        raise ApiError(401, "INVALID_TOKEN", "유효하지 않은 refresh token입니다.")

    # 회전: 기존 토큰을 폐기하고 새 access/refresh를 발급한다.
    row.revoked_at = _now()
    data = _build_session_data(db, user)
    db.commit()
    return RefreshResponse(data=data)


@router.post("/logout", response_model=MessageResponse)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> MessageResponse:
    # 멱등: 존재하지 않거나 이미 폐기된 토큰이어도 성공으로 응답한다.
    token_hash = hash_token(payload.refresh_token)
    row = db.query(RefreshToken).filter_by(token_hash=token_hash).one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = _now()
        db.commit()
    return MessageResponse(data=MessageData(message="로그아웃되었습니다."))


# ---------------------------------------------------------------------------
# 이메일 검증(OTP)
# ---------------------------------------------------------------------------
@router.post("/email-verifications", response_model=EmailVerificationRequestResponse, status_code=201)
def request_email_verification(
    payload: EmailVerificationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> EmailVerificationRequestResponse:
    settings = get_settings()
    email = payload.email.strip()
    if not _EMAIL_RE.match(email):
        raise validation_error(
            "이메일 형식이 올바르지 않습니다.", details=[{"field": "email", "reason": "invalid_format"}]
        )
    normalized_email = _normalize_email(email)

    # 이미 가입된 이메일이면 검증 코드를 보내지 않고 즉시 안내(중복 확인 기능 흡수).
    if db.query(User).filter_by(normalized_email=normalized_email).one_or_none() is not None:
        raise ApiError(409, "EMAIL_ALREADY_EXISTS", "이미 사용 중인 이메일입니다.")

    row = db.query(EmailVerification).filter_by(normalized_email=normalized_email).one_or_none()

    # 재전송 rate-limit: 마지막 발송 후 일정 시간 지나야 다시 보낼 수 있다.
    resend_interval = settings.email_verification_resend_interval_seconds
    if row is not None and row.last_sent_at is not None:
        elapsed = (_now() - _aware(row.last_sent_at)).total_seconds()
        if elapsed < resend_interval:
            raise ApiError(
                429,
                "TOO_MANY_REQUESTS",
                f"잠시 후 다시 시도해 주세요. ({int(resend_interval - elapsed)}초 남음)",
            )

    code = generate_numeric_code()
    ttl = settings.email_verification_code_ttl_seconds
    now = _now()

    if row is None:
        row = EmailVerification(normalized_email=normalized_email)
        db.add(row)
    # 재전송이면 기존 행을 새 코드로 덮어쓰고 시도·검증 상태를 초기화한다.
    row.code_hash = hash_token(code)
    row.expires_at = now + timedelta(seconds=ttl)
    row.attempts = 0
    row.verified_at = None
    row.last_sent_at = now

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "CONFLICT", "요청을 처리할 수 없습니다. 다시 시도해 주세요.")

    # 메일 발송은 응답 지연·실패가 요청을 막지 않도록 백그라운드로 보낸다.
    background_tasks.add_task(
        send_email,
        email,
        "[MultiCloud Ops] 이메일 인증 코드",
        f"인증 코드는 {code} 입니다. {ttl // 60}분 안에 입력해 주세요.",
    )

    return EmailVerificationRequestResponse(
        data=EmailVerificationRequestData(expires_in=ttl, resend_available_in=resend_interval)
    )


@router.post("/email-verifications/verify", response_model=EmailVerifyResponse)
def verify_email_verification(
    payload: EmailVerifyRequest, db: Session = Depends(get_db)
) -> EmailVerifyResponse:
    settings = get_settings()
    normalized_email = _normalize_email(payload.email)
    row = db.query(EmailVerification).filter_by(normalized_email=normalized_email).one_or_none()

    if row is None or _aware(row.expires_at) <= _now():
        raise ApiError(400, "VERIFICATION_CODE_EXPIRED", "인증 코드가 만료되었거나 존재하지 않습니다. 다시 요청해 주세요.")
    if row.attempts >= settings.email_verification_max_attempts:
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "시도 횟수를 초과했습니다. 코드를 다시 요청해 주세요.")

    if not verify_token(payload.code.strip(), row.code_hash):
        row.attempts += 1
        db.commit()
        raise ApiError(400, "INVALID_VERIFICATION_CODE", "인증 코드가 올바르지 않습니다.")

    row.verified_at = _now()
    db.commit()
    return EmailVerifyResponse(data=EmailVerifyData(verified=True))


# ---------------------------------------------------------------------------
# 회원가입 (이메일 검증 게이트 적용)
# ---------------------------------------------------------------------------
@router.post("/sign-up", response_model=SignUpResponse, status_code=201)
def sign_up(payload: SignUpRequest, db: Session = Depends(get_db)) -> SignUpResponse:
    settings = get_settings()
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

    # 이메일 검증 게이트: 최근 window 안에 검증 완료된 레코드가 있어야 가입할 수 있다.
    verification = db.query(EmailVerification).filter_by(normalized_email=normalized_email).one_or_none()
    verified_at = _aware(verification.verified_at) if verification else None
    window = settings.email_verification_valid_window_seconds
    if verified_at is None or (_now() - verified_at).total_seconds() > window:
        raise ApiError(
            422,
            "EMAIL_NOT_VERIFIED",
            "이메일 인증을 먼저 완료해 주세요.",
            details=[{"field": "email", "reason": "not_verified"}],
        )

    user = User(
        email=email,
        normalized_email=normalized_email,
        password_hash=hash_password(payload.password),
        name=payload.name.strip(),
        affiliation_type=payload.affiliation_type,
        affiliation_name=affiliation_name,
    )
    db.add(user)
    # 검증 레코드는 재사용되지 않도록 소비(삭제)한다.
    if verification is not None:
        db.delete(verification)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "EMAIL_ALREADY_EXISTS", "이미 사용 중인 이메일입니다.") from exc
    db.refresh(user)

    return SignUpResponse(data=_serialize_user(user))


# ---------------------------------------------------------------------------
# 비밀번호 재설정
# ---------------------------------------------------------------------------
@router.post("/password-reset", response_model=MessageResponse, status_code=202)
def request_password_reset(
    payload: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> MessageResponse:
    settings = get_settings()
    normalized_email = _normalize_email(payload.email)
    user = db.query(User).filter_by(normalized_email=normalized_email).one_or_none()

    # 이메일 존재 여부를 노출하지 않도록, 계정이 있든 없든 동일한 성공 메시지를 반환한다.
    if user is not None and user.status != "withdrawn":
        token = generate_token()
        ttl = settings.password_reset_token_ttl_seconds
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=_now() + timedelta(seconds=ttl),
            )
        )
        db.commit()
        reset_link = f"{settings.frontend_base_url}/password-reset.html?token={token}"
        background_tasks.add_task(
            send_email,
            user.email,
            "[MultiCloud Ops] 비밀번호 재설정 안내",
            f"아래 링크에서 비밀번호를 재설정해 주세요({ttl // 60}분 내 유효):\n\n{reset_link}",
        )

    return MessageResponse(
        data=MessageData(message="입력하신 이메일로 재설정 안내를 발송했습니다(가입된 계정인 경우).")
    )


@router.post("/password-reset/confirm", response_model=MessageResponse)
def confirm_password_reset(
    payload: PasswordResetConfirmRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    if not is_strong_password(payload.new_password):
        raise validation_error(
            "비밀번호는 8자 이상이며 영문/숫자/기호 중 2종 이상을 포함해야 합니다.",
            details=[{"field": "new_password", "reason": "too_weak"}],
        )

    token_hash = hash_token(payload.token)
    row = db.query(PasswordResetToken).filter_by(token_hash=token_hash).one_or_none()
    if row is None or row.used_at is not None or _aware(row.expires_at) <= _now():
        raise ApiError(400, "INVALID_RESET_TOKEN", "유효하지 않거나 만료된 재설정 링크입니다.")

    user = db.get(User, row.user_id)
    if user is None or user.status == "withdrawn":
        raise ApiError(400, "INVALID_RESET_TOKEN", "유효하지 않거나 만료된 재설정 링크입니다.")

    user.password_hash = hash_password(payload.new_password)
    row.used_at = _now()
    # 비밀번호가 바뀌면 기존 세션(refresh token)을 전부 폐기한다.
    now = _now()
    for rt in db.query(RefreshToken).filter_by(user_id=user.id, revoked_at=None).all():
        rt.revoked_at = now
    db.commit()

    return MessageResponse(data=MessageData(message="비밀번호가 변경되었습니다. 다시 로그인해 주세요."))
