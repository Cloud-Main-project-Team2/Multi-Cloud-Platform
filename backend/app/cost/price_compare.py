"""`POST /provisioning/price-comparisons` — 유사 사양 3사 월 정가 비교(PR 8 · 명세 v1.2 §10.2 ·
docs/비용_개발문서/05_API계약.md §3-2 · 확정 12).

- 계약(요청·응답)은 §10.2 그대로다. 계산은 app/pricing.py(정가표) 하나로 한다 — 화면의 정가 추정과
  같은 표라 숫자가 어긋나지 않는다.
- **월간 정가 차이만** 낸다. 당월 영향은 산출하지 않는다.
- 정가표에 없는 조합은 `estimated_monthly_cost: null` + "견적 불가: 정가표에 없는 조합". 0으로 채우지
  않는다.
- 사양 매핑은 프론트 `provisioning.js`의 SPEC_TIERS와 같은 표(경량/표준/고성능)다 — 3사의 실제
  vCPU·메모리가 등급 안에서도 다르므로 `assumptions`에 매핑 기준과 각 SKU의 실제 사양을 적는다.
- 스토리지·네트워크·백업 등 부속 요금은 미포함이라고 항상 적는다.
- 이 라우터는 별도 파일(app/routers/price_comparisons.py)에 두어 routers/provisioning.py(조은솔님)를
  건드리지 않지만, `/provisioning/*` 경로 소유는 그쪽이라 **병합 전 공유가 필요하다**.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.pricing import HOURS_PER_MONTH, estimate_monthly_cost_usd

COMPUTE_SERVICE_CODE = {"aws": "ec2", "azure": "vm", "gcp": "compute_engine"}

# 등급 → provider별 SKU(실제 vCPU·GiB). provisioning.js SPEC_TIERS와 값을 맞춘다(2026-09-18 정정본).
SPEC_TIERS: list[dict] = [
    {"key": "light", "label": "경량", "sku": {
        "aws": ("t3.micro", 2, 1), "azure": ("B1s", 1, 1), "gcp": ("e2-micro", 2, 1)}},
    {"key": "standard", "label": "표준", "sku": {
        "aws": ("t3.medium", 2, 4), "azure": ("B2s", 2, 4), "gcp": ("e2-medium", 2, 4)}},
    {"key": "high", "label": "고성능", "sku": {
        "aws": ("t3.large", 2, 8), "azure": ("B4ms", 4, 16), "gcp": ("e2-standard-4", 4, 16)}},
]

REGION_GROUPS: dict[str, dict[str, str]] = {
    "northeast_asia": {"aws": "ap-northeast-2", "azure": "koreacentral", "gcp": "asia-northeast3"},
    "north_america": {"aws": "us-east-1", "azure": "eastus", "gcp": "us-central1"},
}

ASSUMPTION_ADDONS = "스토리지·네트워크 등 부속 요금 미포함"
ASSUMPTION_UNAVAILABLE = "견적 불가: 정가표에 없는 조합"


def pick_tier(vcpu: int, memory_gib: Decimal) -> tuple[dict, bool]:
    """(등급, 정확히 일치했는가). 어느 provider의 실제 사양과라도 정확히 맞으면 그 등급, 아니면
    vCPU·메모리 거리가 가장 가까운 등급."""
    for tier in SPEC_TIERS:
        for _p, (_sku, v, m) in tier["sku"].items():
            if v == vcpu and Decimal(m) == memory_gib:
                return tier, True

    def distance(tier: dict) -> Decimal:
        best = None
        for _p, (_sku, v, m) in tier["sku"].items():
            d = abs(Decimal(v) - vcpu) + abs(Decimal(m) - memory_gib) / Decimal(4)
            best = d if best is None else min(best, d)
        return best

    return min(SPEC_TIERS, key=distance), False


def compare(
    *, category: str, region_group: str, vcpu: int, memory_gib: Decimal, hours: Decimal, providers: list[str]
) -> dict:
    regions = REGION_GROUPS[region_group]
    tier, exact = pick_tier(vcpu, memory_gib)
    basis = (
        f"유사 사양 매핑 기준: 요청 {vcpu} vCPU · {memory_gib} GiB → 등급 '{tier['label']}'"
        + ("" if exact else " (정확히 일치하는 SKU 없음 — 가장 가까운 등급)")
    )
    items = []
    for provider in providers:
        service_code = COMPUTE_SERVICE_CODE.get(provider)
        common = [f"{hours} hours/month", ASSUMPTION_ADDONS, basis]
        if category != "compute" or service_code is None:
            items.append({
                "provider": provider, "service_code": service_code or "", "sku": None, "estimated_monthly_cost": None,
                "cost_kind": "list_price_estimate", "source": "provider_price_catalog",
                "assumptions": common + [ASSUMPTION_UNAVAILABLE + f" (category={category})"],
            })
            continue
        sku, v, m = tier["sku"][provider]
        region = regions[provider]
        monthly_730 = estimate_monthly_cost_usd(provider, service_code, {"instance_type": sku, "region": region})
        assumptions = common + [f"{provider} {sku} 실제 사양 {v} vCPU · {m} GiB · 리전 {region}"]
        if monthly_730 is None:
            estimate = None
            assumptions.append(ASSUMPTION_UNAVAILABLE)
        else:
            estimate = (monthly_730 / HOURS_PER_MONTH * hours).quantize(Decimal("0.000001"))
        items.append({
            "provider": provider, "service_code": service_code, "sku": sku,
            "estimated_monthly_cost": None if estimate is None else str(estimate),
            "cost_kind": "list_price_estimate", "source": "provider_price_catalog", "assumptions": assumptions,
        })
    return {"currency": "USD", "as_of": dt.datetime.now(dt.timezone.utc), "items": items}
