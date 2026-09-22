"""팀·예산 API 9종 스키마(PR 7 — docs/비용_개발문서/05_API계약.md §6).

팀은 한 사용자가 연결한 클라우드 계정의 묶음이지 권한 모델이 아니다(RBAC 없음). 예산은
`team_budgets` 이력 행이고 한도를 바꿀 때 기존 행을 수정하지 않는다(확정 8·ADR-025).
금액은 전부 6자리 소수 문자열이다(`03` §4 — Number로 바꾸는 순간 큰 금액에서 자리가 틀어진다).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator

PERIOD_TYPES = ("monthly", "quarterly", "annual", "custom")
THRESHOLDS = (80, 100)


def _validate_currency(value: str) -> str:
    v = (value or "").strip().upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError("currency는 ISO 4217 3글자여야 합니다.")
    return v


def _validate_amount(value: str) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("limit_amount는 숫자 문자열이어야 합니다.") from exc
    if amount <= 0:
        raise ValueError("limit_amount는 0보다 커야 합니다.")
    return str(amount.quantize(Decimal("0.000001")))


# --- 팀 -----------------------------------------------------------------------------------


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    currency: str = "USD"

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name은 비어 있을 수 없습니다.")
        return v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        return _validate_currency(v)


class TeamPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    currency: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name은 비어 있을 수 없습니다.")
        return v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return None if v is None else _validate_currency(v)


class TeamAccountsPutRequest(BaseModel):
    cloud_account_ids: list[str] = Field(default_factory=list)


class TeamAccountOut(BaseModel):
    cloud_account_id: str
    provider: str
    external_account_id: str
    account_label: str | None
    currency: str | None  # 첫 수집에서 확인된 청구 통화 — 수집 전에는 null(ADR-020)
    currency_matches_team: bool | None


class ActiveBudgetBrief(BaseModel):
    id: str
    period_type: str
    limit_amount: str
    currency: str
    start_date: str
    end_date: str | None


class TeamOut(BaseModel):
    id: str
    name: str
    currency: str
    account_count: int
    accounts: list[TeamAccountOut]
    active_budget: ActiveBudgetBrief | None
    created_at: str | None
    updated_at: str | None


class UnassignedOut(BaseModel):
    account_count: int
    accounts: list[TeamAccountOut]


class TeamListData(BaseModel):
    items: list[TeamOut]
    unassigned: UnassignedOut  # 항상 있다 — 팀이 하나도 없어도(확정 7)
    total: int
    pagination: None = None


class TeamListResponse(BaseModel):
    data: TeamListData


class TeamDetailResponse(BaseModel):
    data: TeamOut


# --- 예산 -----------------------------------------------------------------------------------


class BudgetCreateRequest(BaseModel):
    period_type: str
    start_date: dt.date
    end_date: dt.date | None = None  # custom만. 제외 경계로 저장한다
    limit_amount: str
    currency: str | None = None  # 비우면 팀 통화 — 기본 USD를 박으면 KRW 팀에서 조용히 어긋난다

    @field_validator("period_type")
    @classmethod
    def _period_type(cls, v: str) -> str:
        if v not in PERIOD_TYPES:
            raise ValueError("period_type은 monthly|quarterly|annual|custom 중 하나여야 합니다.")
        return v

    @field_validator("limit_amount", mode="before")
    @classmethod
    def _amount(cls, v) -> str:
        return _validate_amount(v)

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return None if v is None else _validate_currency(v)


class BudgetPatchRequest(BaseModel):
    """오타 수정용 — 예정(upcoming) 예산만 허용한다. 이미 시작된 예산의 한도는 새 행을 POST한다.
    start_date·period_type·currency는 여기 없다 — 보내면 라우터가 409로 안내한다."""

    model_config = ConfigDict(extra="allow")  # start_date 등을 보내면 라우터가 409로 안내한다(422가 아니라)

    limit_amount: str | None = None
    end_date: dt.date | None = None  # custom만

    @field_validator("limit_amount", mode="before")
    @classmethod
    def _amount(cls, v) -> str | None:
        return None if v is None else _validate_amount(v)


class BudgetOut(BaseModel):
    id: str
    team_id: str
    period_type: str
    start_date: str
    end_date: str | None
    limit_amount: str
    currency: str
    state: str  # upcoming | in_progress | ended
    created_at: str | None
    updated_at: str | None


class BudgetListData(BaseModel):
    items: list[BudgetOut]
    total: int
    pagination: None = None


class BudgetListResponse(BaseModel):
    data: BudgetListData


class BudgetDetailResponse(BaseModel):
    data: BudgetOut


# --- budget-status --------------------------------------------------------------------------


class BudgetStatusBudget(BaseModel):
    id: str
    period_type: str
    limit_amount: str
    currency: str
    period_start: str
    period_end: str  # 제외 경계
    period_state: str  # upcoming | in_progress | ended
    basis_date: str  # 이 응답이 기준으로 삼은 날(기본 오늘). 과거 기준일 재계산은 지금 데이터로 다시 센 것이다


class BudgetStatusUsage(BaseModel):
    currency: str
    amount: str | None
    basis: str  # usage_before_credits 고정(확정 5)
    net_amount: str | None
    as_of: str | None
    is_estimated: bool


class BudgetStatusForecast(BaseModel):
    amount: str
    ratio_pct: str
    based_through: str


class BudgetStatusThreshold(BaseModel):
    percent: int
    crossed: bool
    crossed_at: str | None
    notified: bool


class BudgetStatusExcludedAccount(BaseModel):
    cloud_account_id: str
    provider: str
    account_label: str | None
    currency: str | None
    reason: str


class BudgetStatusData(BaseModel):
    team_id: str
    budget: BudgetStatusBudget | None
    usage: BudgetStatusUsage | None
    ratio_pct: str | None
    forecast: BudgetStatusForecast | None
    thresholds: list[BudgetStatusThreshold]
    excluded_accounts: list[BudgetStatusExcludedAccount]
    computable: bool
    reason_code: str | None
    staleness_threshold_hours: int


class BudgetStatusResponse(BaseModel):
    data: BudgetStatusData
