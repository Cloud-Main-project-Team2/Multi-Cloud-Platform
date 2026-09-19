"""`POST /api/v1/provisioning/price-comparisons`(PR 8) — 명세 §10.2에 계약만 있고 라우터가 없던 것.

routers/provisioning.py(조은솔님 소유)를 수정하지 않기 위해 별도 파일로 둔다. 경로 소유는 그쪽이므로
병합 전 공유가 필요하다(2026-09-19). 계산은 app/cost/price_compare.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.cost.price_compare import REGION_GROUPS, compare
from app.db import get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.models import ServiceCatalog, User
from app.schemas.price_comparisons import (
    PriceComparisonData,
    PriceComparisonItem,
    PriceComparisonRequest,
    PriceComparisonResponse,
)
from app.serialization import iso_z

router = APIRouter(prefix="/api/v1", tags=["provisioning"])


@router.post("/provisioning/price-comparisons", response_model=PriceComparisonResponse)
def create_price_comparison(
    payload: PriceComparisonRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PriceComparisonResponse:
    try:
        catalog_id = int(payload.service_catalog_id)
    except ValueError as exc:
        raise validation_error("service_catalog_id는 숫자 ID여야 합니다.", details=[{"field": "service_catalog_id", "reason": "invalid"}]) from exc
    service = db.get(ServiceCatalog, catalog_id)
    if service is None:
        raise ApiError(404, "SERVICE_NOT_FOUND", "서비스를 찾을 수 없습니다.")
    spec = payload.common_spec
    if spec.region_group not in REGION_GROUPS:
        raise validation_error(
            "region_group은 " + "|".join(REGION_GROUPS) + " 중 하나여야 합니다.",
            details=[{"field": "common_spec.region_group", "reason": "invalid"}],
        )
    result = compare(
        category=service.category, region_group=spec.region_group, vcpu=spec.vcpu, memory_gib=spec.memory_gib,
        hours=spec.usage_hours_per_month, providers=payload.providers,
    )
    return PriceComparisonResponse(data=PriceComparisonData(
        currency=result["currency"], as_of=iso_z(result["as_of"]),
        items=[PriceComparisonItem(**it) for it in result["items"]],
    ))
