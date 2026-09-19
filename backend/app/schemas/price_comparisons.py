"""`POST /provisioning/price-comparisons` 스키마 — 명세 v1.2 §10.2 그대로(PR 8)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class PriceComparisonCommonSpec(BaseModel):
    region_group: str
    vcpu: int = Field(ge=1, le=256)
    memory_gib: Decimal = Field(gt=0)
    usage_hours_per_month: Decimal = Field(default=Decimal("730"), gt=0, le=Decimal("744"))


class PriceComparisonRequest(BaseModel):
    service_catalog_id: str
    common_spec: PriceComparisonCommonSpec
    providers: list[str] = Field(default_factory=lambda: ["aws", "azure", "gcp"], min_length=1, max_length=3)

    @field_validator("providers")
    @classmethod
    def _providers(cls, v: list[str]) -> list[str]:
        out = []
        for p in v:
            if p not in ("aws", "azure", "gcp"):
                raise ValueError("providers는 aws|azure|gcp만 가능합니다.")
            if p not in out:
                out.append(p)
        return out


class PriceComparisonItem(BaseModel):
    provider: str
    service_code: str
    sku: str | None
    estimated_monthly_cost: str | None  # null = 견적 불가(0이 아니다)
    cost_kind: str
    source: str
    assumptions: list[str]


class PriceComparisonData(BaseModel):
    currency: str
    as_of: str
    items: list[PriceComparisonItem]


class PriceComparisonResponse(BaseModel):
    data: PriceComparisonData
