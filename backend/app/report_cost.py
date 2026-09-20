"""보고서 "비용 요약" 섹션 — 생성 시점 스냅샷 계산(2026-09-19, `kwonhyeong/be-next`).

비용 파트(이승현)의 지시(권형님_보고서_비용연동_개발프롬프트_20260919.md)를 따른다 — 비용
집계·비교·카테고리 매핑은 `app/cost/query.py`의 공통 함수를 **그대로** 재사용하고, 보고서에서
별도 공식으로 다시 계산하지 않는다. 같은 프로세스 안이라 HTTP로 자기 서버를 다시 호출하지
않고 파이썬 함수를 직접 부른다 — 인증 의존성은 자동 적용되지 않지만, `summary()`/`breakdown()`
등은 전부 `user_id`로 `owned_accounts()`를 거쳐 소유권을 강제하므로 호출자가 항상 실제
`current_user.id`를 넘기기만 하면 안전하다(라우터를 우회하지 않음).

**스냅샷을 저장하는 이유**: 사용률/미사용 리소스(app/metrics.py)는 "지금 이 순간" 값을 보여주는
것이 맞지만, 비용은 "그 보고서를 생성한 시점의 값"이 고정돼야 한다는 게 비용 파트의 명시적
요구사항(§9) — 재수집으로 숫자가 바뀌어도 이미 만든 보고서는 그대로여야 한다. 그래서 이 값은
`report_generations.cost_snapshot`에 저장되고, 조회 시점에 다시 계산하지 않는다
(app/routers/reports.py의 `create_report_generation`이 생성/재생성 시에만 호출한다).

**추이 버킷 단위는 기간 길이로 결정한다**(정책 협의 대상 아님 — 표시 방식은 보고서 쪽 재량,
§1 "필드 이름 변경·표시 순서·차트 좌표 계산은 보고서가 수행 가능"): 31일 이하는 daily,
120일 이하는 weekly, 그 이상(반기 등)은 monthly. `cost/query.py`가 반기 개념 자체를 모르므로
(quarterly는 3개월 분기라 반기 대체 아님, §6) 이 매핑은 "보고서가 보여주는 방식"이지 비용
파트의 계약이 아니다.

**범위 밖(이번 라운드에 포함하지 않음, 비용 파트 PR 8 대기)**: 예산 소진율·급증 판정·
`/cost-reports/*` 부품. 이 함수는 문서 §3에서 "현재 사용 가능"으로 확인된 조회 6종 중
summary·breakdown·trend·changes만 쓴다."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.cost.query import CostQuery, breakdown, changes, summary, trend, validate_period
from app.serialization import iso_z

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models import User

_BREAKDOWN_TOP_N = 6
_CHANGES_TOP_N = 10


def _trend_granularity_for_days(days: int) -> str:
    if days <= 31:
        return "daily"
    if days <= 120:
        return "weekly"
    return "monthly"


def _to_jsonable(value: Any) -> Any:
    """cost/query.py의 반환 dict는 금액을 이미 `money()`(str)로 바꿔서 담지만, `as_of`류
    datetime 필드는 그대로 남아 있다 — JSONB 저장 전에 전부 문자열로 바꾼다."""
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dt.datetime):
        return iso_z(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def build_cost_snapshot(
    db: "Session", user: "User", period_from: dt.date, period_to: dt.date, providers: list[str]
) -> dict:
    """`period_from`/`period_to`는 보고서 표시 기준(둘 다 포함, inclusive) — 비용 API는
    `period_end`가 exclusive라 하루를 더해서 넘긴다(§6, "화면 09-01~09-30이면 요청은
    period_end=10-01"). 여기서 변환하므로 호출자는 inclusive 날짜만 넘기면 된다."""
    query_end = period_to + dt.timedelta(days=1)
    # summary()/breakdown() 등은 자체적으로 기간 상한을 검사하지 않는다(그건 router의
    # parse_period() 몫이다) — 여기선 라우터를 거치지 않으므로 같은 검사를 직접 호출한다
    # (비용 파트의 366일 상한·범위 검증을 그대로 재사용, §6).
    validate_period(period_from, query_end)
    q = CostQuery(period_start=period_from, period_end=query_end, providers=providers)
    granularity = _trend_granularity_for_days((query_end - period_from).days)

    snapshot = {
        "summary": summary(db, user.id, q),
        "breakdown_provider": breakdown(db, user.id, q, "provider", _BREAKDOWN_TOP_N, None),
        "breakdown_category": breakdown(db, user.id, q, "category", _BREAKDOWN_TOP_N, None),
        "trend_provider": trend(db, user.id, q, granularity, "provider", None),
        "changes": changes(db, user.id, q, "previous_period", "service", _CHANGES_TOP_N),
        "generated_with": {"granularity": granularity},
    }
    return _to_jsonable(snapshot)
