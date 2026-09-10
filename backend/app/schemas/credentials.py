"""POST /credentials/{provider} 요청 body — docs/01_API_명세서_v1.1.md 6.2절."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CredentialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_account_id: str
    account_label: str | None = None
    name: str
    public_identifier: str | None = None
    secret_payload: dict
    tags: dict = Field(default_factory=dict)
    display_order: int = 0
