"""3사 공통 실측 비용 수집 인터페이스. 어댑터는 "읽어서 표준형으로 돌려주기"만 한다 —
DB를 모르고, 트랜잭션을 모르고, 스케줄을 모른다. 저장(`app/cost/ingest.py`)이 그 표준형을
받아 `cloud_account_costs`에 범위 교체로 반영한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass
class CostRow:
    period_start: dt.date  # 포함
    period_end: dt.date  # 제외 경계
    service: str | None  # None = 계정 단위(귀속 서비스 없음)
    charge_category: str  # usage | credit | refund | tax | other
    amount: Decimal  # 원통화. 음수 허용(크레딧·환불)
    currency: str  # CSP가 준 원통화. 환산하지 않는다
    is_estimated: bool
    source_record_key: str  # 재수집 멱등 키(docs/DB_ERD_v1.2.md Part B R3 §3-2)
    metadata: dict = field(default_factory=dict)


@dataclass
class CostFetchResult:
    rows: list[CostRow]
    currency: str | None  # 이번 수집에서 확인된 청구 통화
    covered_through: dt.date | None
    api_calls: int  # 과금 추적용 — 반드시 센다(Cost Explorer는 요청당 $0.01)
    partial: bool = False
    error_code: str | None = None
    # 이 결과가 저장될 구간을 무엇을 근거로 "확인"이라 부를 수 있는가(app/cost/coverage.py).
    # 기본은 보수적인 observed_only — 새 어댑터가 아무 말도 하지 않으면 판정에 쓰지 않는다.
    # "요청 범위 전체를 확인으로 인정"(complete_range)은 어댑터가 명시적으로 선언해야 한다.
    coverage_basis: str = "observed_only"


# `partial=True`에는 성격이 다른 둘이 섞여 있다.
#   - 받다가 끊겼다(예: 2페이지째 실패) → 진짜 "부분 수신". 저장은 안 하지만 데이터는 있었다.
#   - **시작도 못 했다**(설정 없음·권한 거절·인증 실패) → 받은 게 0건이다.
# 아래 코드는 후자다. 호출부(routers/costs.py·cost/scheduler.py)가 run 상태를 `failed`로 종결해,
# 화면이 "부분 수신(저장 안 됨)" 대신 **설정 필요·권한 없음**을 보여 주게 한다(사용자가 할 일이 다르다).
# 저장 정책은 그대로다 — 어느 쪽이든 행은 쓰지 않는다.
NOT_STARTED_ERROR_CODES = frozenset({
    "COST_SETUP_REQUIRED",
    "CLOUD_PERMISSION_DENIED",
    "PROVIDER_AUTHENTICATION_FAILED",
})


class CostProvider(Protocol):
    def fetch(
        self,
        secret_payload: dict,
        external_account_id: str,
        period_start: dt.date,
        period_end: dt.date,
    ) -> CostFetchResult: ...
