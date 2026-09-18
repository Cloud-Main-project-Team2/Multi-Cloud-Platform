"""동기화(sync)로 발견한 리소스에도 정가 기반 월 예상 비용을 채운다.

`app/pricing.py`의 `estimate_monthly_cost_usd()`는 지금까지 프로비저닝 성공 경로
(`app/routers/provisioning.py`의 `_create_resource_from_job`) 한 곳에서만 호출됐다. 그래서
동기화로 처음 발견한 리소스는 `estimated_monthly_cost`가 항상 비어 있었고, 인벤토리·대시보드의
비용 합계가 과소 집계됐다. 이 모듈은 동기화 지점
(`app/routers/sync_jobs.py`의 `_upsert_discovered_resources`)에서 같은 함수를 호출해 그 간극을
메운다. CSP 비용 API는 호출하지 않는다 — `app/pricing.py`의 정가표만 읽는다.
"""

from __future__ import annotations

import datetime as dt

from app.models import Resource
from app.pricing import estimate_monthly_cost_usd
from app.resource_sync import DiscoveredResource

# 정가 추정으로 덮어써도 되는 cost_source. 저장소에서 resources.cost_source에 실제로 들어가는
# 값은 세 가지뿐이다 — None(provisioning.py의 미지원 조합), "list_price_estimate"(provisioning.py
# 성공 경로), "seed"(seed_mock_data.py의 데모 데이터). 'actual'은 이 컬럼의 값이 아니라
# cloud_resource_costs.cost_kind의 값이다 — 혼동하지 않는다. "seed"나 PR 4 이후의 실측 값은
# 이 함수가 건드리지 않는다: 시드 금액을 정가로 덮으면 시연 화면이 조용히 달라지고, 실측을
# 정가가 덮으면 화면의 MTD가 조용히 정가로 바뀐다 — 두 금액은 성격이 달라 대체 관계가 아니다.
_OVERWRITABLE = (None, "list_price_estimate")


def apply_list_price_estimate(resource: Resource, provider: str, disc: DiscoveredResource, now: dt.datetime) -> None:
    if resource.cost_source not in _OVERWRITABLE:
        return  # 실측·시드가 들어온 행은 그대로 둔다

    estimated = estimate_monthly_cost_usd(provider, disc.service_code, disc.spec or {})

    if estimated is None:
        # 정가표에 없는 조합이다. 0으로 채우지 않고, 이전 추정값이 있었다면 지운다 — 스펙이
        # 바뀌어 표 밖으로 나간 경우 옛 금액이 남아 있으면 그게 더 나쁘다.
        if resource.cost_source == "list_price_estimate":
            resource.estimated_monthly_cost = None
            resource.cost_currency = None
            resource.cost_source = None
            resource.cost_as_of = None
        return

    resource.estimated_monthly_cost = estimated
    resource.cost_currency = "USD"  # 정가표는 USD 고정
    resource.cost_source = "list_price_estimate"
    resource.cost_as_of = now

    # 근거(어떤 스펙으로 얼마를 계산했나)를 남긴다. raw_metadata는 프로비저닝 경로가 terraform
    # outputs를 넣어 두는 칸이므로(provisioning.py의 _create_resource_from_job) 통째로 대입하면
    # 지운다 — 반드시 병합하고 우리 키 하나에만 쓴다.
    meta = dict(resource.raw_metadata or {})
    meta["list_price_spec"] = {
        "spec": disc.spec or {},
        "hours_per_month": "730",
        "assumption": "상시 가동 가정",
    }
    resource.raw_metadata = meta  # JSONB는 새 dict 대입으로만 변경이 감지된다
