from __future__ import annotations

from pydantic import BaseModel, Field


class CreateProvisioningJobRequest(BaseModel):
    credential_id: str
from pydantic import BaseModel, ConfigDict, Field
from typing import Any


class ProvisioningCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: int
    common_spec: dict = Field(default_factory=dict)
    provider_spec: dict = Field(default_factory=dict)


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
    message: str | None = None


class ProvisioningJobOut(BaseModel):
    id: str
    credential_id: str
    service_catalog_id: str
    workspace_name: str
    common_spec: dict
    provider_spec: dict
    status: str
    progress_percent: int
    created_resource_count: int
    result: dict | None
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

