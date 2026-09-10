from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
