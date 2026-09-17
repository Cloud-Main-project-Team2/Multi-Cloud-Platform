from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1)


class SignUpRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    affiliation_type: Literal["company", "individual"]
    affiliation_name: str | None = Field(default=None, max_length=200)


class UpdateMeRequest(BaseModel):
    # 마이페이지 계정 정보 수정 — 이름·소속만. 이메일은 로그인 식별자라 이 경로로 바꾸지 않고,
    # 비밀번호도 범위 밖(별도 재설정 흐름). 인라인 폼이 항상 전체 편집 대상 값을 함께 보낸다.
    name: str = Field(min_length=1, max_length=100)
    affiliation_type: Literal["company", "individual"]
    affiliation_name: str | None = Field(default=None, max_length=200)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    affiliation_type: str
    affiliation_name: str | None
    status: str
    created_at: str
    updated_at: str
    withdrawn_at: str | None


class LoginResponseData(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    user: UserOut


class LoginResponse(BaseModel):
    data: LoginResponseData


class SignUpResponse(BaseModel):
    data: UserOut


class MeResponse(BaseModel):
    data: UserOut


# --- refresh token ---
class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class RefreshResponse(BaseModel):
    data: LoginResponseData


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


# --- 이메일 검증(OTP) ---
class EmailVerificationRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)


class EmailVerificationRequestData(BaseModel):
    expires_in: int
    resend_available_in: int


class EmailVerificationRequestResponse(BaseModel):
    data: EmailVerificationRequestData


class EmailVerifyRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    code: str = Field(min_length=1, max_length=12)


class EmailVerifyData(BaseModel):
    verified: bool


class EmailVerifyResponse(BaseModel):
    data: EmailVerifyData


# --- 비밀번호 재설정 ---
class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class MessageData(BaseModel):
    message: str


class MessageResponse(BaseModel):
    data: MessageData
