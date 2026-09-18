from __future__ import annotations

from pydantic import BaseModel


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
