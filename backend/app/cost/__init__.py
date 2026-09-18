"""실측 비용 수집 어댑터 등록표. 이 표가 `UNSUPPORTED` 판정의 1순위 근거다(ADR-040) —
`GET /costs/capabilities`(PR 5)는 이 표부터 확인하고, 그다음에야 credential 검증·
`cost_ingestion_runs` 마지막 행을 본다. Azure·GCP는 후속(확정 18)에서 같은 `CostProvider`
인터페이스로 어댑터만 추가한다.
"""

from __future__ import annotations

from app.cost.aws_cost import AwsCostProvider
from app.cost.base import CostProvider

COST_ADAPTERS: dict[str, type[CostProvider]] = {
    "aws": AwsCostProvider,
}


def is_cost_supported(provider: str) -> bool:
    return provider in COST_ADAPTERS
