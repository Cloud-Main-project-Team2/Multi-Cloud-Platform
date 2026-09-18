from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CostIngestionRunCreateRequest(BaseModel):
    cloud_account_ids: list[str] | None = None
    period_start: str | None = None
    period_end: str | None = None


class CostIngestionRunCreateItem(BaseModel):
    id: str
    cloud_account_id: str
    status: str
    status_url: str


class CostIngestionRunSkipped(BaseModel):
    cloud_account_id: str
    reason_code: str
    next_allowed_at: str | None = None


class CostIngestionRunCreateData(BaseModel):
    items: list[CostIngestionRunCreateItem]
    skipped: list[CostIngestionRunSkipped]


class CostIngestionRunCreateResponse(BaseModel):
    data: CostIngestionRunCreateData


class CostIngestionRunOut(BaseModel):
    id: str
    cloud_account_id: str
    provider: str
    trigger_type: str
    status: str
    period_start: str
    period_end: str
    requested_at: str
    started_at: str | None
    finished_at: str | None
    api_calls: int
    records_replaced: int
    error_code: str | None
    error_message: str | None


class CostIngestionRunDetailResponse(BaseModel):
    data: CostIngestionRunOut


class CostIngestionRunListData(BaseModel):
    items: list[CostIngestionRunOut]
    total: int
    pagination: None = None


class CostIngestionRunListResponse(BaseModel):
    data: CostIngestionRunListData


# --- 조회 6종(PR 5) — 응답 모양이 복잡한 하위 구조는 dict[str, Any]로 둔다(이 저장소의
# 기존 관례 — app/schemas/{provisioning,credentials,security_groups}.py 참고). 값 자체는
# app/cost/query.py·app/routers/costs.py가 docs/01_API_Specification_v1.2.md §11의 JSON
# 예시와 같은 모양으로 만든다. ----------------------------------------------------------


class CostCapabilityItem(BaseModel):
    cloud_account_id: str
    provider: str
    external_account_id: str
    account_label: str | None
    team_id: str | None
    status: str
    as_of: str | None
    ingestion_running: bool
    cost_read: bool | None
    capability_source: str
    currency: str | None
    setup_hint: str | None
    last_error_code: str | None


class CostCapabilitiesData(BaseModel):
    staleness_threshold_hours: int
    items: list[CostCapabilityItem]
    total: int
    pagination: None = None


class CostCapabilitiesResponse(BaseModel):
    data: CostCapabilitiesData


class CostSummaryData(BaseModel):
    period: dict[str, Any]
    as_of: str | None
    staleness_threshold_hours: int
    kpis: dict[str, Any]
    accounts: list[dict[str, Any]]
    excluded: dict[str, Any]
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class CostSummaryResponse(BaseModel):
    data: CostSummaryData


class CostTrendData(BaseModel):
    granularity: str
    group_by: str
    currency: str | None
    currency_selection: dict[str, Any]
    staleness_threshold_hours: int
    series: list[dict[str, Any]]
    budget_line: dict[str, Any] | None = None
    missing_days: list[str] = Field(default_factory=list)


class CostTrendResponse(BaseModel):
    data: CostTrendData


class CostBreakdownData(BaseModel):
    dimension: str
    currency: str | None
    currency_selection: dict[str, Any]
    cost_kind: str
    total: str
    items: list[dict[str, Any]]
    rest: dict[str, Any]
    unallocated: dict[str, Any]
    estimate_unavailable_count: int = 0


class CostBreakdownResponse(BaseModel):
    data: CostBreakdownData


class CostChangesData(BaseModel):
    current: dict[str, Any]
    previous: dict[str, Any]
    comparable: bool
    currency: str | None
    totals: dict[str, Any]
    increases: list[dict[str, Any]]
    decreases: list[dict[str, Any]]
    new_items: list[dict[str, Any]]


class CostChangesResponse(BaseModel):
    data: CostChangesData


class CostCollectionStatusItem(BaseModel):
    cloud_account_id: str
    provider: str
    status: str
    as_of: str | None
    ingestion_running: bool
    last_success_at: str | None
    last_attempt_at: str | None
    last_error_code: str | None
    next_manual_allowed_at: str | None
    covered_through: str | None
    missing_days: list[str] = Field(default_factory=list)


class CostCollectionStatusData(BaseModel):
    staleness_threshold_hours: int
    items: list[CostCollectionStatusItem]
    total: int
    pagination: None = None


class CostCollectionStatusResponse(BaseModel):
    data: CostCollectionStatusData
