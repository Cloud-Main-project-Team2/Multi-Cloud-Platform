"""실측 비용 수집 어댑터 등록표. 이 표가 `UNSUPPORTED` 판정의 1순위 근거다(ADR-040) —
`GET /costs/capabilities`(PR 5)는 이 표부터 확인하고, 그다음에야 credential 검증·
`cost_ingestion_runs` 마지막 행을 본다. Azure는 2026-09-23에 같은 `CostProvider` 인터페이스로 추가했고(app/cost/azure_cost.py),
GCP는 후속이다.
"""

from __future__ import annotations

from app.cost.aws_cost import AwsCostProvider
from app.cost.azure_cost import AzureCostProvider
from app.cost.base import CostProvider

COST_ADAPTERS: dict[str, type[CostProvider]] = {
    "aws": AwsCostProvider,
    # ⚠️ 등록 = "수집기가 구현돼 있다"일 뿐이다. 실제 호출 여부는 app/cost/gating.py가 정하며
    # 기본값(COST_INGEST_PROVIDERS=aws)에서는 Azure 호출이 0건이다(A-3).
    "azure": AzureCostProvider,
}

# 저장 출처(cloud_account_costs.source) — 수동·자동 경로가 **같은 값**을 써야 범위 교체가 서로를
# 덮는다. 예전에는 두 라우터가 각자 f"{provider}_cost_explorer"를 만들어, Azure면 실제 API 이름과
# 다른 "azure_cost_explorer"가 저장될 뻔했다. 정본은 이 표 하나다.
COST_SOURCES: dict[str, str] = {
    "aws": "aws_cost_explorer",
    "azure": "azure_cost_management",
    "gcp": "gcp_bigquery_billing",      # GCP 수집기는 아직 없다 — 이름만 미리 고정한다
}


def is_cost_supported(provider: str) -> bool:
    return provider in COST_ADAPTERS


def cost_source_for(provider: str) -> str:
    """그 provider의 저장 출처. 모르는 provider도 기존 규칙과 같은 모양을 유지한다."""
    return COST_SOURCES.get(provider, f"{provider}_cost_explorer")
