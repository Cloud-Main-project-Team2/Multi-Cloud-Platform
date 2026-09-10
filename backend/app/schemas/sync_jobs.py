from __future__ import annotations

from pydantic import BaseModel


class SyncJobCreateRequest(BaseModel):
    # null/생략 = 인증 사용자의 검증 완료 연결 계정 전체, 배열 = 지정한 내 계정만(§9.2)
    cloud_account_ids: list[str] | None = None


class SyncJobCreateData(BaseModel):
    id: str
    status: str
    requested_at: str
    status_url: str


class SyncJobCreateResponse(BaseModel):
    data: SyncJobCreateData


class SyncJobItemError(BaseModel):
    code: str
    message: str | None = None


class SyncJobItemOut(BaseModel):
    id: str
    cloud_account_id: str
    credential_id: str | None
    provider: str
    status: str
    resources_discovered: int
    resources_created: int
    resources_updated: int
    resources_marked_stale: int
    error: SyncJobItemError | None
    started_at: str | None
    finished_at: str | None


class ProviderSummary(BaseModel):
    provider: str
    status: str
    completed: int
    total: int


class SyncJobDetail(BaseModel):
    id: str
    status: str
    requested_at: str
    started_at: str | None
    finished_at: str | None
    provider_summary: list[ProviderSummary]
    items: list[SyncJobItemOut]


class SyncJobDetailResponse(BaseModel):
    data: SyncJobDetail


class SyncJobListData(BaseModel):
    items: list[SyncJobDetail]
    total: int
    pagination: None = None


class SyncJobListResponse(BaseModel):
    data: SyncJobListData
