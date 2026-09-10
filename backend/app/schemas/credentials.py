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


class VerifyResponse(BaseModel):
    data: VerifyResponseData
