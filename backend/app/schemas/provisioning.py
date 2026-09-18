from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.errors import ErrorExplanationOut


class CreateProvisioningJobRequest(BaseModel):
    credential_id: str
    common_spec: dict[str, Any] = Field(default_factory=dict)
    provider_spec: dict[str, Any] = Field(default_factory=dict)


class ProvisioningJobCreateData(BaseModel):
    id: str
    status: str
    created_at: str
    status_url: str


class ProvisioningJobCreateResponse(BaseModel):
    data: ProvisioningJobCreateData


class ProvisioningJobError(BaseModel):
    code: str
    message: str | None = None  # 저장된 실패 원문(terraform/CSP stderr, redact됨)
    explanation: ErrorExplanationOut | None = None  # code별 고정 설명
    specific_reason: str | None = None  # 원문을 번역한 구체 원인(없으면 null)


class ProvisioningJobOut(BaseModel):
    id: str
    credential_id: str | None  # credential이 삭제되면 null(2026-09-18, job 기록은 그대로 남음)
    service_catalog_id: str
    workspace_name: str
    common_spec: dict[str, Any]
    provider_spec: dict[str, Any]
    status: str
    progress_percent: int
    created_resource_count: int
    result: dict[str, Any] | None
    error: ProvisioningJobError | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class ProvisioningJobResponse(BaseModel):
    data: ProvisioningJobOut


class ProvisioningJobListData(BaseModel):
    items: list[ProvisioningJobOut]
    total: int
    pagination: None = None


class ProvisioningJobListResponse(BaseModel):
    data: ProvisioningJobListData
