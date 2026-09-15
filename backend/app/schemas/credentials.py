from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateCredentialRequest(BaseModel):
    external_account_id: str = Field(min_length=1, max_length=255)
    account_label: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    public_identifier: str | None = Field(default=None, max_length=255)
    secret_payload: dict[str, Any]
    tags: dict[str, Any] = Field(default_factory=dict)
    display_order: int = Field(default=0, ge=0)


class PatchCloudAccountRequest(BaseModel):
    account_label: str | None = Field(default=None, max_length=200)


class PatchCredentialRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tags: dict[str, Any] | None = None
    display_order: int | None = Field(default=None, ge=0)
    public_identifier: str | None = Field(default=None, max_length=255)
    secret_payload: dict[str, Any] | None = None


class CredentialOrderItem(BaseModel):
    credential_id: str
    display_order: int = Field(ge=0)


class CredentialOrderRequest(BaseModel):
    items: list[CredentialOrderItem] = Field(min_length=1)


class CloudAccountOut(BaseModel):
    id: str
    provider: str
    external_account_id: str
    account_label: str | None
    created_at: str
    updated_at: str


class CredentialOut(BaseModel):
    id: str
    cloud_account_id: str
    name: str
    masked_public_identifier: str | None
    permission_scope: dict[str, Any]
    verified: bool
    verified_at: str | None
    tags: dict[str, Any]
    display_order: int
    created_at: str
    updated_at: str
    # 이 자격 증명이 어떤 인증 방식인지. 화면에서 "레거시 키" 배지를 띄우는 데 쓴다.
    auth_type: str = "access_key"
    # 방금 수행한 검증이 실패한 이유. **저장하지 않는다** — 등록/수정/재검증 응답에만 담기고
    # 목록 조회에서는 항상 None이다. AccessDenied는 원인이 구분되지 않아 사용자가 스스로
    # 좁힐 수 있도록 안내 문구를 함께 내려준다.
    verification_error_code: str | None = None
    verification_error_message: str | None = None


class CloudAccountResponse(BaseModel):
    data: CloudAccountOut


class CloudAccountListData(BaseModel):
    items: list[CloudAccountOut]
    total: int
    pagination: None = None


class CloudAccountListResponse(BaseModel):
    data: CloudAccountListData


class CredentialResponse(BaseModel):
    data: CredentialOut


class CredentialListData(BaseModel):
    items: list[CredentialOut]
    total: int
    pagination: None = None


class CredentialListResponse(BaseModel):
    data: CredentialListData


class VerifyResponseData(BaseModel):
    credential_id: str
    verified: bool
    verified_at: str | None
    permission_scope: dict[str, Any]
    verification_error_code: str | None = None
    verification_error_message: str | None = None


class AwsDelegationSetupData(BaseModel):
    """AWS 역할 위임 온보딩에 필요한 값. 사용자는 이걸 보고 자기 계정에 역할을 만든다."""

    platform_account_id: str
    external_id: str
    role_name_prefix: str
    suggested_role_name: str
    trust_policy: dict[str, Any]
    managed_policy_arns: list[str]
    inline_actions: list[str]
    iam_console_url: str
    troubleshooting: list[str]


class AwsDelegationSetupResponse(BaseModel):
    data: AwsDelegationSetupData


class VerifyResponse(BaseModel):
    data: VerifyResponseData
