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
    user: UserOut


class LoginResponse(BaseModel):
    data: LoginResponseData


class SignUpResponse(BaseModel):
    data: UserOut
