"""비용 조회 6종 공통 — **DB만 읽는다**(ADR-040). `boto3`/`azure`/`google` import 금지 —
위반하면 화면을 새로 고칠 때마다 Cost Explorer 요청이 $0.01씩 나간다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Date, cast, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import validation_error
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Resource

# CSP 원본 서비스 이름 -> service_catalog.category. CE는 "AmazonEC2"를 주고 카탈로그는 "ec2"라
# 1:1이 아니다(05_API계약.md §4-4) — 매핑표는 여기 한 곳에만 둔다. AWS만 구현했다(이번 라운드
# 실측 수집이 AWS뿐이라 Azure·GCP 서비스 이름은 아직 모른다). 매핑에 없는 서비스는 "기타"가
# 아니라 "unallocated"로 보낸다 — "기타"는 상위 N을 넘긴 것이고 "unallocated"는 분류 규칙
# 자체가 없는 것이다(QA-09).
_AWS_SERVICE_TO_CATEGORY = {
    "AmazonEC2": "compute",
    "AmazonRDS": "db_rdbms",
    "AmazonS3": "storage_object",
    "AmazonCloudFront": "cdn",
}

_GRANULARITY_TRUNC = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter"}

MAX_PERIOD_DAYS = 366
DEFAULT_CHARGE_CATEGORIES = ["usage"]


@dataclass
class CostQuery:
    period_start: dt.date
    period_end: dt.date
    providers: list[str] = field(default_factory=list)
    cloud_account_ids: list[int] = field(default_factory=list)
    currency: str | None = None
    charge_categories: list[str] = field(default_factory=lambda: list(DEFAULT_CHARGE_CATEGORIES))
    # team_id 필터는 파라미터로 받아 두되(§4 공통 query 7개 계약), 팀 배정 UI가 아직 없는
    # 이번 라운드(PR 7 이전)에는 실제로 적용하지 않는다 — 모든 계정이 미배정이라 걸러도 무의미하다.


def default_period() -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    return today.replace(day=1), today + dt.timedelta(days=1)


def parse_period(period_start: str | None, period_end: str | None) -> tuple[dt.date, dt.date]:
    start, end = default_period()
    if period_start:
        start = dt.date.fromisoformat(period_start)
    if period_end:
        end = dt.date.fromisoformat(period_end)
    validate_period(start, end)
    return start, end


def validate_period(period_start: dt.date, period_end: dt.date) -> None:
    if period_end <= period_start:
        raise validation_error(
            "period_end는 period_start보다 커야 합니다.",
            details=[{"field": "period_end", "reason": "invalid_range"}],
        )
    if (period_end - period_start).days > MAX_PERIOD_DAYS:
        raise validation_error(
            f"조회 기간은 최대 {MAX_PERIOD_DAYS}일입니다.",
            details=[{"field": "period_end", "reason": "range_too_long"}],
        )


def owned_accounts(db: Session, user_id: int, q: CostQuery) -> list[CloudAccount]:
    query = db.query(CloudAccount).filter(CloudAccount.user_id == user_id)
    if q.providers:
        query = query.filter(CloudAccount.provider.in_(q.providers))
    if q.cloud_account_ids:
        query = query.filter(CloudAccount.id.in_(q.cloud_account_ids))
    return query.order_by(CloudAccount.id).all()


def staleness_threshold_hours() -> int:
    return get_settings().cost_stale_after_hours


def money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


# --- 금액 합산(SQL SUM, GROUP BY currency) ------------------------------------------------


def sum_by_currency(
    db: Session,
    user_id: int,
    q: CostQuery,
    *,
    charge_categories: list[str] | None = None,
    period_end_override: dt.date | None = None,
) -> list[tuple[str, Decimal, bool]]:
    """[(currency, total_amount, is_estimated_any)] — GROUP BY에 currency를 반드시 넣는다.
    빼면 통화가 다른 금액이 한 숫자로 더해진다(이 프로젝트에서 가장 위험한 실수)."""
    period_end = period_end_override or q.period_end
    query = (
        db.query(
            CloudAccountCost.currency,
            func.sum(CloudAccountCost.amount),
            func.bool_or(CloudAccountCost.is_estimated),
        )
        .join(CloudAccount, CloudAccount.id == CloudAccountCost.cloud_account_id)
        .filter(
            CloudAccount.user_id == user_id,
            CloudAccountCost.period_start >= q.period_start,
            CloudAccountCost.period_start < period_end,
        )
    )
    if q.providers:
        query = query.filter(CloudAccount.provider.in_(q.providers))
    if q.cloud_account_ids:
        query = query.filter(CloudAccountCost.cloud_account_id.in_(q.cloud_account_ids))
    cats = charge_categories if charge_categories is not None else q.charge_categories
    if cats:
        query = query.filter(CloudAccountCost.charge_category.in_(cats))
    return query.group_by(CloudAccountCost.currency).all()


def list_price_monthly(db: Session, user_id: int, q: CostQuery) -> tuple[list[tuple[str, Decimal]], int]:
    """CF-004 — `resources.estimated_monthly_cost` 합. 기간·통화·요금분류와 무관하다(§3-1).
    반환: ([(currency, total)], missing_count)."""
    query = (
        db.query(Resource)
        .join(CloudAccount, CloudAccount.id == Resource.cloud_account_id)
        .filter(CloudAccount.user_id == user_id, Resource.deleted_at.is_(None))
    )
    if q.providers:
        query = query.filter(CloudAccount.provider.in_(q.providers))
    if q.cloud_account_ids:
        query = query.filter(Resource.cloud_account_id.in_(q.cloud_account_ids))

    totals: dict[str, Decimal] = {}
    missing = 0
    for resource in query.all():
        if resource.estimated_monthly_cost is None:
            missing += 1
            continue
        currency = resource.cost_currency or "USD"
        totals[currency] = totals.get(currency, Decimal("0")) + resource.estimated_monthly_cost
    return list(totals.items()), missing


def pick_currency(
    per_account_currency: dict[int, str], requested: str | None
) -> tuple[str | None, str, list[dict]]:
    """통화 하나만 고른다(ADR-023). per_account_currency: {cloud_account_id: currency}.
    반환: (선택된 통화, reason, excluded[{currency, account_count}])."""
    if not per_account_currency:
        return None, "only_one", []

    counts: dict[str, int] = {}
    for currency in per_account_currency.values():
        counts[currency] = counts.get(currency, 0) + 1

    if requested:
        chosen, reason = requested, "requested"
    elif len(counts) == 1:
        chosen, reason = next(iter(counts)), "only_one"
    else:
        chosen = max(counts, key=lambda c: counts[c])
        reason = "largest_share"

    excluded = [{"currency": c, "account_count": n} for c, n in counts.items() if c != chosen]
    return chosen, reason, excluded


def accounts_currency_map(db: Session, accounts: list[CloudAccount]) -> dict[int, str]:
    """계정별 최근 확인 통화 — capability.py의 _account_currency와 같은 규칙(최근 as_of 1건)."""
    if not accounts:
        return {}
    account_ids = [a.id for a in accounts]
    rows = (
        db.query(CloudAccountCost.cloud_account_id, CloudAccountCost.currency, CloudAccountCost.as_of)
        .filter(CloudAccountCost.cloud_account_id.in_(account_ids))
        .order_by(CloudAccountCost.cloud_account_id, CloudAccountCost.as_of.desc())
        .all()
    )
    result: dict[int, str] = {}
    for cloud_account_id, currency, _as_of in rows:
        result.setdefault(cloud_account_id, currency)
    return result


def sum_by_account_currency(
    db: Session, user_id: int, q: CostQuery, *, charge_categories: list[str] | None = None
) -> dict[int, tuple[Decimal, str]]:
    """계정별 (금액, 통화). 한 계정·한 기간 안에서 통화가 섞이는 일은 CSP 쪽에서 일어나지
    않는다고 가정한다(청구 계정의 통화는 하나다) — 여러 통화가 나오면 마지막 것으로 덮인다."""
    query = (
        db.query(CloudAccountCost.cloud_account_id, CloudAccountCost.currency, func.sum(CloudAccountCost.amount))
        .join(CloudAccount, CloudAccount.id == CloudAccountCost.cloud_account_id)
        .filter(
            CloudAccount.user_id == user_id,
            CloudAccountCost.period_start >= q.period_start,
            CloudAccountCost.period_start < q.period_end,
        )
    )
    if q.providers:
        query = query.filter(CloudAccount.provider.in_(q.providers))
    if q.cloud_account_ids:
        query = query.filter(CloudAccountCost.cloud_account_id.in_(q.cloud_account_ids))
    cats = charge_categories if charge_categories is not None else q.charge_categories
    if cats:
        query = query.filter(CloudAccountCost.charge_category.in_(cats))
    rows = query.group_by(CloudAccountCost.cloud_account_id, CloudAccountCost.currency).all()
    return {account_id: (total, currency) for account_id, currency, total in rows}


def resource_counts_by_account(db: Session, user_id: int, q: CostQuery) -> dict[int, dict]:
    query = (
        db.query(Resource.cloud_account_id, func.count(Resource.id), func.max(Resource.last_synced_at))
        .join(CloudAccount, CloudAccount.id == Resource.cloud_account_id)
        .filter(CloudAccount.user_id == user_id, Resource.deleted_at.is_(None))
    )
    if q.providers:
        query = query.filter(CloudAccount.provider.in_(q.providers))
    if q.cloud_account_ids:
        query = query.filter(Resource.cloud_account_id.in_(q.cloud_account_ids))
    rows = query.group_by(Resource.cloud_account_id).all()
    return {account_id: {"count": count, "synced_at": synced_at} for account_id, count, synced_at in rows}


def days_with_data(db: Session, user_id: int, q: CostQuery, currency: str | None = None) -> set[dt.date]:
    query = (
        db.query(CloudAccountCost.period_start)
        .join(CloudAccount, CloudAccount.id == CloudAccountCost.cloud_account_id)
        .filter(
            CloudAccount.user_id == user_id,
            CloudAccountCost.period_start >= q.period_start,
            CloudAccountCost.period_start < q.period_end,
        )
        .distinct()
    )
    if currency:
        query = query.filter(CloudAccountCost.currency == currency)
    if q.providers:
        query = query.filter(CloudAccount.provider.in_(q.providers))
    if q.cloud_account_ids:
        query = query.filter(CloudAccountCost.cloud_account_id.in_(q.cloud_account_ids))
    return {row[0] for row in query.all()}


def missing_days(db: Session, user_id: int, q: CostQuery, currency: str | None = None) -> list[str]:
    """기간 안에서 수집 행이 하나도 없는 날짜들. 오늘·미래는 "아직 수집될 수 없음"이라 결측으로
    세지 않는다."""
    have = days_with_data(db, user_id, q, currency)
    today = dt.date.today()
    out: list[str] = []
    d = q.period_start
    while d < q.period_end and d < today:
        if d not in have:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    return (next_month - dt.date(year, month, 1)).days


def forecast_month_end(db: Session, user_id: int, q: CostQuery) -> list[dict]:
    """CF-003 — 이번 달 진행 중일 때만 낸다(§4-2). MTD(어제까지) ÷ 경과일수 × 이번 달 총일수."""
    today = dt.date.today()
    this_month_start = today.replace(day=1)
    is_current_month = q.period_start == this_month_start and q.period_end == today + dt.timedelta(days=1)
    if not is_current_month or today.day == 1:
        return []

    days_elapsed = today.day - 1  # 어제까지
    days_in_month = _days_in_month(today.year, today.month)
    based_through = today - dt.timedelta(days=1)

    rows = sum_by_currency(db, user_id, q, period_end_override=today)
    out = []
    for currency, total, _est in rows:
        forecast = (total / Decimal(days_elapsed) * Decimal(days_in_month)).quantize(Decimal("0.000001"))
        out.append(
            {
                "cost_kind": "actual",
                "currency": currency,
                "amount": money(forecast),
                "method": "mtd_prorated",
                "based_through": based_through.isoformat(),
            }
        )
    return out


def _category_for(provider: str, service: str | None) -> str | None:
    if provider == "aws":
        return _AWS_SERVICE_TO_CATEGORY.get(service or "")
    return None  # Azure·GCP 서비스 이름 매핑은 그 CSP 수집기가 생긴 뒤(후속)


def breakdown(
    db: Session, user_id: int, q: CostQuery, dimension: str, top_n: int, currency_param: str | None
) -> dict:
    accounts = owned_accounts(db, user_id, q)
    per_account_currency = accounts_currency_map(db, accounts)
    chosen_currency, reason, excluded = pick_currency(per_account_currency, currency_param)

    empty_rest = {"label": "기타(0종)", "amount": money(Decimal("0")), "count": 0, "share_pct": "0.0"}
    if chosen_currency is None:
        return {
            "dimension": dimension, "currency": None,
            "currency_selection": {"reason": reason, "excluded": excluded},
            "cost_kind": "actual", "total": money(Decimal("0")),
            "items": [], "rest": empty_rest,
            "unallocated": {"amount": money(Decimal("0")), "reason": None},
            "estimate_unavailable_count": 0,
        }

    query = (
        db.query(
            CloudAccount.provider, CloudAccountCost.cloud_account_id, CloudAccountCost.service,
            func.sum(CloudAccountCost.amount),
        )
        .join(CloudAccount, CloudAccount.id == CloudAccountCost.cloud_account_id)
        .filter(
            CloudAccount.user_id == user_id,
            CloudAccountCost.period_start >= q.period_start,
            CloudAccountCost.period_start < q.period_end,
            CloudAccountCost.currency == chosen_currency,
            CloudAccountCost.charge_category == "usage",  # 비중은 항상 usage 기준
        )
    )
    if q.providers:
        query = query.filter(CloudAccount.provider.in_(q.providers))
    if q.cloud_account_ids:
        query = query.filter(CloudAccountCost.cloud_account_id.in_(q.cloud_account_ids))
    rows = query.group_by(CloudAccount.provider, CloudAccountCost.cloud_account_id, CloudAccountCost.service).all()

    buckets: dict[str, Decimal] = {}
    unallocated = Decimal("0")
    unallocated_reason: str | None = None
    for provider, account_id, service, amount in rows:
        if dimension == "service":
            key = service or "__unallocated__"
            if service is None:
                unallocated += amount
                unallocated_reason = "no_service"
                continue
        elif dimension == "account":
            key = str(account_id)
        elif dimension == "category":
            category = _category_for(provider, service)
            if category is None:
                unallocated += amount
                unallocated_reason = "no_category_mapping"
                continue
            key = category
        elif dimension == "team":
            key = "unassigned"  # 팀 배정은 PR 7 이전이라 전부 미배정
        else:  # provider(기본)
            key = provider
        buckets[key] = buckets.get(key, Decimal("0")) + amount

    total = sum(buckets.values(), Decimal("0")) + unallocated
    sorted_items = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
    top_items = sorted_items[:top_n]
    rest_items = sorted_items[top_n:]
    rest_amount = sum((v for _, v in rest_items), Decimal("0"))

    def pct(amount: Decimal) -> str:
        if total <= 0:
            return "0.0"
        return str((amount / total * 100).quantize(Decimal("0.1")))

    return {
        "dimension": dimension, "currency": chosen_currency,
        "currency_selection": {"reason": reason, "excluded": excluded},
        "cost_kind": "actual", "total": money(total),
        "items": [{"key": k, "label": k, "amount": money(v), "share_pct": pct(v)} for k, v in top_items],
        "rest": {
            "label": f"기타({len(rest_items)}종)", "amount": money(rest_amount),
            "count": len(rest_items), "share_pct": pct(rest_amount),
        },
        "unallocated": {"amount": money(unallocated), "reason": unallocated_reason if unallocated > 0 else None},
        "estimate_unavailable_count": 0,
    }


def _dimension_col(dimension: str):
    return {
        "service": CloudAccountCost.service,
        "account": CloudAccountCost.cloud_account_id,
        "provider": CloudAccount.provider,
    }.get(dimension, CloudAccountCost.service)


def _shift_one_month_back(d: dt.date) -> dt.date:
    year, month = d.year, d.month - 1
    if month == 0:
        month, year = 12, year - 1
    day = min(d.day, _days_in_month(year, month))
    return dt.date(year, month, day)


def changes(db: Session, user_id: int, q: CostQuery, compare: str, dimension: str, top_n: int) -> dict:
    accounts = owned_accounts(db, user_id, q)
    per_account_currency = accounts_currency_map(db, accounts)
    chosen_currency, _reason, _excluded = pick_currency(per_account_currency, None)

    days = (q.period_end - q.period_start).days
    if compare == "previous_month":
        prev_start = _shift_one_month_back(q.period_start)
        prev_end = _shift_one_month_back(q.period_end)
    else:  # previous_period
        prev_end = q.period_start
        prev_start = prev_end - dt.timedelta(days=days)
    prev_days = (prev_end - prev_start).days
    comparable = prev_days == days

    def dim_sums(period_start: dt.date, period_end: dt.date) -> dict[str, Decimal]:
        col = _dimension_col(dimension)
        query = (
            db.query(col, func.sum(CloudAccountCost.amount))
            .join(CloudAccount, CloudAccount.id == CloudAccountCost.cloud_account_id)
            .filter(
                CloudAccount.user_id == user_id,
                CloudAccountCost.period_start >= period_start,
                CloudAccountCost.period_start < period_end,
                CloudAccountCost.charge_category == "usage",
            )
        )
        if chosen_currency:
            query = query.filter(CloudAccountCost.currency == chosen_currency)
        if q.providers:
            query = query.filter(CloudAccount.provider.in_(q.providers))
        if q.cloud_account_ids:
            query = query.filter(CloudAccountCost.cloud_account_id.in_(q.cloud_account_ids))
        return {str(k): v for k, v in query.group_by(col).all() if k is not None}

    current_sums = dim_sums(q.period_start, q.period_end)
    current_total = sum(current_sums.values(), Decimal("0"))

    if not comparable:
        return {
            "current": {"start": q.period_start.isoformat(), "end": q.period_end.isoformat(), "days": days},
            "previous": {"start": prev_start.isoformat(), "end": prev_end.isoformat(), "days": prev_days},
            "comparable": False, "currency": chosen_currency,
            "totals": {"current": money(current_total), "previous": None, "delta": None, "delta_pct": None},
            "increases": [], "decreases": [], "new_items": [],
        }

    previous_sums = dim_sums(prev_start, prev_end)
    previous_total = sum(previous_sums.values(), Decimal("0"))
    delta = current_total - previous_total
    delta_pct = str((delta / previous_total * 100).quantize(Decimal("0.1"))) if previous_total > 0 else None

    increases, decreases, new_items = [], [], []
    for key in set(current_sums) | set(previous_sums):
        cur = current_sums.get(key)
        prev = previous_sums.get(key)
        if prev is None:
            new_items.append({"key": key, "label": key, "current": money(cur), "previous": None})
            continue
        d_amount = (cur or Decimal("0")) - prev
        d_pct = str((d_amount / prev * 100).quantize(Decimal("0.1"))) if prev > 0 else None
        item = {
            "key": key, "label": key, "current": money(cur or Decimal("0")), "previous": money(prev),
            "delta": money(d_amount), "delta_pct": d_pct, "is_new": False,
        }
        (increases if d_amount > 0 else decreases).append(item)

    increases.sort(key=lambda i: Decimal(i["delta"]), reverse=True)
    decreases.sort(key=lambda i: Decimal(i["delta"]))

    return {
        "current": {"start": q.period_start.isoformat(), "end": q.period_end.isoformat(), "days": days},
        "previous": {"start": prev_start.isoformat(), "end": prev_end.isoformat(), "days": prev_days},
        "comparable": True, "currency": chosen_currency,
        "totals": {
            "current": money(current_total), "previous": money(previous_total),
            "delta": money(delta), "delta_pct": delta_pct,
        },
        "increases": increases[:top_n], "decreases": decreases[:top_n], "new_items": new_items[:top_n],
    }


def trend(db: Session, user_id: int, q: CostQuery, granularity: str, group_by: str, currency_param: str | None) -> dict:
    accounts = owned_accounts(db, user_id, q)
    per_account_currency = accounts_currency_map(db, accounts)
    chosen_currency, reason, excluded = pick_currency(per_account_currency, currency_param)

    result = {
        "currency": chosen_currency, "currency_selection": {"reason": reason, "excluded": excluded},
        "series": [], "missing_days": [],
    }
    if chosen_currency is None:
        return result

    trunc_unit = _GRANULARITY_TRUNC.get(granularity, "day")
    bucket_col = cast(func.date_trunc(trunc_unit, CloudAccountCost.period_start), Date)
    group_col = {"account": CloudAccountCost.cloud_account_id, "service": CloudAccountCost.service}.get(
        group_by, CloudAccount.provider
    )

    query = (
        db.query(group_col, bucket_col, func.sum(CloudAccountCost.amount), func.bool_or(CloudAccountCost.is_estimated))
        .join(CloudAccount, CloudAccount.id == CloudAccountCost.cloud_account_id)
        .filter(
            CloudAccount.user_id == user_id,
            CloudAccountCost.period_start >= q.period_start,
            CloudAccountCost.period_start < q.period_end,
            CloudAccountCost.currency == chosen_currency,
        )
    )
    if q.providers:
        query = query.filter(CloudAccount.provider.in_(q.providers))
    if q.cloud_account_ids:
        query = query.filter(CloudAccountCost.cloud_account_id.in_(q.cloud_account_ids))
    if q.charge_categories:
        query = query.filter(CloudAccountCost.charge_category.in_(q.charge_categories))
    rows = query.group_by(group_col, bucket_col).order_by(bucket_col).all()

    series_map: dict[str, dict[str, Decimal]] = {}
    est_map: dict[str, bool] = {}
    for key, bucket, amount, is_est in rows:
        key = str(key) if key is not None else "unallocated"
        series_map.setdefault(key, {})[bucket.isoformat()] = amount
        if is_est:
            est_map[key] = True

    series = []
    for key, points in series_map.items():
        labels = sorted(points.keys())
        series.append({
            "key": key, "label": key, "cost_kind": "actual",
            "points": [
                {
                    "period_start": label,
                    "period_end": _bucket_end(dt.date.fromisoformat(label), granularity).isoformat(),
                    "amount": money(points[label]),
                    "is_estimated": est_map.get(key, False),
                }
                for label in labels
            ],
        })

    result["series"] = series
    result["missing_days"] = missing_days(db, user_id, q, chosen_currency)
    return result


def _bucket_end(start: dt.date, granularity: str) -> dt.date:
    if granularity == "weekly":
        return start + dt.timedelta(days=7)
    if granularity == "quarterly":
        month = start.month + 3
        year = start.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        return dt.date(year, month, 1)
    if granularity == "monthly":
        if start.month == 12:
            return dt.date(start.year + 1, 1, 1)
        return dt.date(start.year, start.month + 1, 1)
    return start + dt.timedelta(days=1)  # daily


def summary(db: Session, user_id: int, q: CostQuery) -> dict:
    from app.cost.capability import account_capability

    accounts = owned_accounts(db, user_id, q)

    mtd_actual_rows = sum_by_currency(db, user_id, q)
    mtd_net_rows = sum_by_currency(
        db, user_id, q, charge_categories=["usage", "credit", "refund", "tax", "other"]
    )
    list_price_rows, missing_count = list_price_monthly(db, user_id, q)
    forecast_rows = forecast_month_end(db, user_id, q)

    kpis = {
        "mtd_actual": [
            {
                "cost_kind": "actual", "currency": currency, "amount": money(total),
                "is_estimated": bool(is_est), "basis": "usage_before_credits",
            }
            for currency, total, is_est in mtd_actual_rows
        ],
        "mtd_net": [
            {"cost_kind": "actual", "currency": currency, "amount": money(total), "is_estimated": bool(is_est)}
            for currency, total, is_est in mtd_net_rows
        ],
        "list_price_monthly": [
            {
                "cost_kind": "list_price_estimate", "currency": currency, "amount": money(total),
                "assumptions": ["730 hours/month"], "missing_count": missing_count,
            }
            for currency, total in list_price_rows
        ],
        "forecast_month_end": forecast_rows,
    }

    actual_by_account = sum_by_account_currency(db, user_id, q)
    list_price_by_account: dict[int, Decimal] = {}
    for resource in (
        db.query(Resource)
        .join(CloudAccount, CloudAccount.id == Resource.cloud_account_id)
        .filter(CloudAccount.user_id == user_id, Resource.deleted_at.is_(None))
        .all()
    ):
        if resource.estimated_monthly_cost is not None:
            list_price_by_account[resource.cloud_account_id] = (
                list_price_by_account.get(resource.cloud_account_id, Decimal("0")) + resource.estimated_monthly_cost
            )
    resource_counts = resource_counts_by_account(db, user_id, q)

    accounts_out = []
    excluded_count = 0
    excluded_reasons: dict[str, int] = {}
    as_of_values: list[dt.datetime] = []

    for account in accounts:
        cap = account_capability(db, account)
        if cap["as_of"] is not None:
            as_of_values.append(cap["as_of"])
        if cap["status"] not in ("CONNECTED_OK", "CONNECTED_EMPTY", "CONNECTED_PARTIAL"):
            excluded_count += 1
            excluded_reasons[cap["status"]] = excluded_reasons.get(cap["status"], 0) + 1

        actual_amount, actual_currency = actual_by_account.get(account.id, (None, None))
        rc = resource_counts.get(account.id, {"count": 0, "synced_at": None})

        accounts_out.append({
            "cloud_account_id": str(account.id), "provider": account.provider,
            "account_label": account.account_label, "team_id": str(account.team_id) if account.team_id else None,
            "status": cap["status"], "as_of": cap["as_of"], "ingestion_running": cap["ingestion_running"],
            "currency": actual_currency or cap["currency"],
            "actual": money(actual_amount), "list_price_estimate": money(list_price_by_account.get(account.id)),
            "resource_count": rc["count"], "resources_synced_at": rc["synced_at"],
            "is_estimated": actual_amount is not None,
        })

    warnings = []
    gaps = missing_days(db, user_id, q)
    if gaps:
        warnings.append({
            "code": "PARTIAL_PERIOD",
            "message": f"{gaps[0]} ~ {gaps[-1]} 구간 중 일부가 수집되지 않았습니다.",
            "missing_days": gaps,
        })

    display_end = q.period_end - dt.timedelta(days=1)
    return {
        "period": {
            "start": q.period_start.isoformat(), "end": q.period_end.isoformat(),
            "display": f"{q.period_start.isoformat()} ~ {display_end.isoformat()}",
        },
        "as_of": max(as_of_values) if as_of_values else None,
        "staleness_threshold_hours": staleness_threshold_hours(),
        "kpis": kpis,
        "accounts": accounts_out,
        "excluded": {"accounts": excluded_count, "reason_counts": excluded_reasons},
        "warnings": warnings,
    }


def collection_status(db: Session, user_id: int, q: CostQuery) -> list[dict]:
    from app.cost.capability import account_capability

    accounts = owned_accounts(db, user_id, q)
    items = []
    for account in accounts:
        cap = account_capability(db, account)
        last_attempt = (
            db.query(CostIngestionRun)
            .filter(CostIngestionRun.cloud_account_id == account.id)
            .order_by(CostIngestionRun.requested_at.desc())
            .first()
        )
        last_success = (
            db.query(CostIngestionRun)
            .filter(
                CostIngestionRun.cloud_account_id == account.id,
                CostIngestionRun.status.in_(("success", "partial_success")),
            )
            .order_by(CostIngestionRun.finished_at.desc())
            .first()
        )
        next_allowed = None
        if last_success is not None and last_success.trigger_type == "manual" and last_success.finished_at:
            next_allowed = last_success.finished_at + dt.timedelta(hours=1)
        covered_through = None
        if last_success is not None:
            covered_through = (last_success.period_end - dt.timedelta(days=1)).isoformat()

        items.append({
            "cloud_account_id": account.id, "provider": account.provider,
            "status": cap["status"], "as_of": cap["as_of"], "ingestion_running": cap["ingestion_running"],
            "last_success_at": last_success.finished_at if last_success else None,
            "last_attempt_at": last_attempt.requested_at if last_attempt else None,
            "last_error_code": cap["last_error_code"],
            "next_manual_allowed_at": next_allowed,
            "covered_through": covered_through,
            # 계정별 결측일 상세는 이번 라운드에서 생략한다 — summary의 warnings가 같은 신호를
            # 이미 전체 범위로 준다(중복 계산 대신 단순화).
            "missing_days": [],
        })
    return items
