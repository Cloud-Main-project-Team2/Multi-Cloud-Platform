"""수집 확인(coverage) — "이 계정의 이 날은 수집이 확인됐는가"를 한 곳에서 정의한다(PR 8).

예산 결측 판정(budget.py)과 급증 판정(anomaly.py)이 같은 함수를 쓴다 — 두 곳이 다르게 세면 같은
날이 한쪽에선 결측, 다른 쪽에선 정상이 된다.

정의: 날 d가 수집 확인됨 = (d에 cloud_account_costs 행이 있다) OR (status='success'인 수집 run의
[period_start, period_end)에 d가 든다). $0인 날은 행이 안 생기므로 행 유무만 보면 CONNECTED_EMPTY
계정이 영원히 결측이 된다 — 그래서 run 범위를 함께 본다. 서비스별 행이 여러 개여도, run이 겹쳐도
날짜는 집합이라 한 번만 센다.

날짜 경계는 UTC다(2026-09-19 결정) — 수집 행의 period_start는 CSP 일 단위(UTC)이고, 서버 로컬
날짜를 섞으면 자정 전후 한 시간대에 판정이 흔들린다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.models import CloudAccountCost, CostIngestionRun


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def covered_days(db: Session, cloud_account_id: int, start: dt.date, end: dt.date) -> set[dt.date]:
    """[start, end) 안에서 수집 확인된 날짜 집합."""
    if end <= start:
        return set()
    days = {
        row[0]
        for row in db.query(CloudAccountCost.period_start)
        .filter(
            CloudAccountCost.cloud_account_id == cloud_account_id,
            CloudAccountCost.period_start >= start,
            CloudAccountCost.period_start < end,
        )
        .distinct()
        .all()
    }
    runs = (
        db.query(CostIngestionRun.period_start, CostIngestionRun.period_end)
        .filter(
            CostIngestionRun.cloud_account_id == cloud_account_id,
            CostIngestionRun.status == "success",
            CostIngestionRun.period_start < end,
            CostIngestionRun.period_end > start,
        )
        .all()
    )
    for run_start, run_end in runs:
        d = max(run_start, start)
        stop = min(run_end, end)
        while d < stop:
            days.add(d)
            d += dt.timedelta(days=1)
    return days


def missing_days(db: Session, cloud_account_id: int, start: dt.date, end: dt.date) -> list[dt.date]:
    """[start, end) 중 수집 확인이 안 된 날짜(정렬)."""
    have = covered_days(db, cloud_account_id, start, end)
    out = []
    d = start
    while d < end:
        if d not in have:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def covered_days_since_first(db: Session, cloud_account_id: int, end: dt.date) -> set[dt.date]:
    """계정의 첫 수집 확인일부터 `end`(제외)까지의 수집 확인 날짜 집합 — 급증 판정이 "판정일 이하
    이력 12일"을 날짜별로 셀 때 한 번만 읽고 재사용한다."""
    earliest_row = (
        db.query(CloudAccountCost.period_start)
        .filter(CloudAccountCost.cloud_account_id == cloud_account_id)
        .order_by(CloudAccountCost.period_start)
        .first()
    )
    earliest_run = (
        db.query(CostIngestionRun.period_start)
        .filter(CostIngestionRun.cloud_account_id == cloud_account_id, CostIngestionRun.status == "success")
        .order_by(CostIngestionRun.period_start)
        .first()
    )
    candidates = [r[0] for r in (earliest_row, earliest_run) if r is not None]
    if not candidates:
        return set()
    return covered_days(db, cloud_account_id, min(candidates), end)


def eligible_end_for_today() -> dt.date:
    """급증 판정 가능한 날의 제외 경계(오늘 UTC 기준) — anomaly.eligible_end의 편의 함수."""
    from app.cost.anomaly import eligible_end

    return eligible_end(utc_today())
