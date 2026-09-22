"""급증 탐지·검토 큐 스키마(PR 8 — docs/비용_개발문서/05_API계약.md §7-1·§7-2).

계약에 없는 필드 2개를 2026-09-19 결정으로 추가했다: `held[]`(기준선 미완성·판정일 미수집으로 보류한
날 — 조용히 빠지지 않게) · `unsupported_currency[]`(최소 차액이 정해지지 않은 통화의 계정 — KRW 미결).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

REVIEW_STATUSES = ("open", "investigating", "resolved")
RESOLUTIONS = ("too_small", "expected", "unexpected")


class AnomalyRule(BaseModel):
    baseline_days: int
    min_delta_amount: str | None
    min_increase_pct: int
    min_history_days: int
    exclude_recent_days: int
    currency: str
    charge_category: list[str]


class AnomalyReviewBrief(BaseModel):
    item_id: str
    status: str
    resolution: str | None


class RelatedChange(BaseModel):
    type: str
    id: str
    service_code: str
    finished_at: str | None


class AnomalyItemOut(BaseModel):
    source_key: str
    cloud_account_id: str
    provider: str
    service: str
    date: str
    currency: str
    amount: str
    baseline_amount: str
    delta: str
    delta_pct: str | None  # null = 기준선 0(신규 비용 발생) — 0%도, 계산 오류도 아니다
    review: AnomalyReviewBrief | None
    related_changes: list[RelatedChange]
    label: str
    is_sample_data: bool


class InsufficientHistoryOut(BaseModel):
    cloud_account_id: str
    days_available: int
    days_required: int


class HeldDay(BaseModel):
    date: str
    reason: str  # baseline_incomplete | day_not_collected


class HeldOut(BaseModel):
    cloud_account_id: str
    days: list[HeldDay]


class UnsupportedCurrencyOut(BaseModel):
    cloud_account_id: str
    provider: str
    currency: str | None


class AnomaliesData(BaseModel):
    rule: AnomalyRule
    items: list[AnomalyItemOut]
    insufficient_history: list[InsufficientHistoryOut]
    held: list[HeldOut]
    unsupported_currency: list[UnsupportedCurrencyOut]
    total: int
    pagination: None = None


class AnomaliesResponse(BaseModel):
    data: AnomaliesData


# --- 검토 큐 --------------------------------------------------------------------------------


class ReviewItemCreateRequest(BaseModel):
    source_type: str = Field(pattern="^cost_anomaly$")  # 확장 source_type은 아직 없다
    source_key: str = Field(min_length=3, max_length=512)
    note: str | None = Field(default=None, max_length=2000)


class ReviewItemPatchRequest(BaseModel):
    status: str | None = None
    resolution: str | None = None
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is not None and v not in REVIEW_STATUSES:
            raise ValueError("status는 open|investigating|resolved 중 하나여야 합니다.")
        return v

    @field_validator("resolution")
    @classmethod
    def _resolution(cls, v: str | None) -> str | None:
        if v is not None and v not in RESOLUTIONS:
            raise ValueError("resolution은 too_small|expected|unexpected 중 하나여야 합니다.")
        return v


class ReviewItemOut(BaseModel):
    id: str
    source_type: str
    source_key: str
    status: str
    resolution: str | None
    note: str | None
    resolved_at: str | None
    created_at: str | None
    updated_at: str | None


class ReviewItemListData(BaseModel):
    items: list[ReviewItemOut]
    total: int
    pagination: None = None


class ReviewItemListResponse(BaseModel):
    data: ReviewItemListData


class ReviewItemDetailResponse(BaseModel):
    data: ReviewItemOut
