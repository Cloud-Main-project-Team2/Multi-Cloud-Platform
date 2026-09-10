from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CloudAccountBrief(BaseModel):
    id: str
    provider: str
    external_account_id: str
    account_label: str | None


class ServiceBrief(BaseModel):
    id: str
    service_code: str
    category: str
    display_name: str


class CostSummary(BaseModel):
    estimated_monthly_cost: str | None
    collected_cost_amount: str | None
    currency: str | None
    period_start: str | None
    period_end: str | None
    as_of: str | None
    source: str | None


class ResourceOut(BaseModel):
    id: str
    cloud_account: CloudAccountBrief
    service: ServiceBrief
    external_resource_id: str
    original_resource_type: str
    name: str | None
    region: str | None
    status: str | None
    cost_summary: CostSummary | None
    tags: dict[str, Any]
    first_seen_at: str
    last_seen_at: str
    is_stale: bool
    deleted_at: str | None
    last_synced_at: str | None


class ResourceListData(BaseModel):
    items: list[ResourceOut]
    total: int
    pagination: None = None


class ResourceListResponse(BaseModel):
    data: ResourceListData


class ResourceResponse(BaseModel):
    data: ResourceOut


class ProviderCount(BaseModel):
    provider: str
    count: int


class ResourceSummaryData(BaseModel):
    total_resources: int
    active_resources: int
    stale_resources: int
    last_synced_at: str | None
    by_provider: list[ProviderCount]


class ResourceSummaryResponse(BaseModel):
    data: ResourceSummaryData


class ResourceActionRequest(BaseModel):
    action: Literal["start", "stop", "delete"]
    resource_ids: list[str] = Field(min_length=1)
    # S3/GCS 버킷이 비어 있지 않아 삭제가 거부됐을 때(BucketNotEmpty) 클라이언트가 재요청하는
    # 흐름을 지원하기 위한 확장 필드 — §8.5 canonical 예시엔 없지만 이번 세션에서 추가(§1 결정).
    force_empty: bool = False


class ActionResultError(BaseModel):
    code: str


class ActionResultItem(BaseModel):
    resource_id: str
    status: Literal["success", "rejected", "failed"]
    error: ActionResultError | None = None


class ResourceActionData(BaseModel):
    action: str
    results: list[ActionResultItem]


class ResourceActionResponse(BaseModel):
    data: ResourceActionData
