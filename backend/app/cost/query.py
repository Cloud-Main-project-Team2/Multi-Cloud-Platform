"""비용 조회 6종 공통 — **DB만 읽는다**(ADR-040). `boto3`/`azure`/`google` import 금지 —
위반하면 화면을 새로 고칠 때마다 Cost Explorer 요청이 $0.01씩 나간다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Date, cast, func, or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.cost import is_cost_supported
from app.cost.coverage import covered_days, utc_today
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
    # team_id 필터(§4 공통 query 7개 계약)는 여기 두지 않는다 — 라우터가 resolve_team_scope()로
    # cloud_account_ids에 풀어 넣는다(PR 7). 아래 필터 지점 18곳이 전부 cloud_account_ids만 본다.


# 팀 필터 결과가 빈 집합일 때 넣는 값 — 빈 리스트는 "필터 없음"이라 구분이 필요하다. id는 1부터
# 시작하므로 -1은 어떤 계정과도 일치하지 않는다.
EMPTY_SCOPE_IDS = [-1]
UNASSIGNED_TEAM = "unassigned"


def resolve_team_scope(db: Session, user_id: int, team_ids: list[str], account_ids: list[int]) -> list[int]:
    """`team_id` 필터를 그 사용자의 계정 id 목록으로 바꾼다. `unassigned`는 team_id IS NULL.
    명시적 cloud_account_id가 함께 오면 교집합. 결과가 비면 EMPTY_SCOPE_IDS."""
    ids: list[int] = []
    include_unassigned = False
    for raw in team_ids:
        if raw == UNASSIGNED_TEAM:
            include_unassigned = True
            continue
        try:
            ids.append(int(raw))
        except ValueError as exc:
            raise validation_error(
                "team_id는 숫자 ID 또는 unassigned여야 합니다.", details=[{"field": "team_id", "reason": "invalid"}]
            ) from exc
    conds = []
    if ids:
        conds.append(CloudAccount.team_id.in_(ids))
    if include_unassigned:
        conds.append(CloudAccount.team_id.is_(None))
    query = db.query(CloudAccount.id).filter(CloudAccount.user_id == user_id, or_(*conds))
    if account_ids:
        query = query.filter(CloudAccount.id.in_(account_ids))
    scoped = [row[0] for row in query.all()]
    return scoped or list(EMPTY_SCOPE_IDS)


def default_period() -> tuple[dt.date, dt.date]:
    today = utc_today()  # 집계 날짜는 UTC(coverage.py 결정) — 컨테이너 TZ(KST)를 따르지 않는다
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
    if q.currency:  # '청구 통화' 필터(05 §4 공통 query · 08 §5-2 build_cost_filters)
        query = query.filter(CloudAccountCost.currency == q.currency)
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
    if q.currency:
        query = query.filter(CloudAccountCost.currency == q.currency)
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


# --- 수집 확인(coverage) — 계정 단위, UTC ---------------------------------------------------
#
# 예전 days_with_data()는 "필터 안 어느 계정이든 그 날 비용 행이 하나라도 있으면 수집됨"으로
# 봤다. 그러면 ① 계정 A만 수집되고 B는 안 된 날이 정상으로 보이고, ② 성공했지만 $0이라 행이
# 없는 날이 결측으로 보이고, ③ 서버 로컬(KST) 날짜와 UTC 집계일이 섞였다. 지금은 예산·급증과
# 같은 coverage.covered_days(행 OR success run 범위, UTC)를 계정마다 따로 본다.
#
# 한계(그대로 둔다): "그 날 행이 있다"는 그 run이 그 날을 저장했다는 뜻이지 서비스별 완전성의
# 증명은 아니다 — 저장이 계정·기간 단위 범위 교체(08 §4-4, partial은 저장 안 함)라 실무상
# 같은 뜻으로 본다. partial_success run은 행을 저장하지 않으므로 그 구간은 "미확인"이다.

MISSING_DAYS_LIST_LIMIT = 31


def account_coverage(
    db: Session, cloud_account_id: int, start: dt.date, end: dt.date, *, today: dt.date | None = None
) -> dict:
    """[start, end) 중 **완료된 날**(UTC 오늘·미래 제외)의 수집 확인 결과.
    days=완료된 날 수 · covered=확인된 날 수 · missing_count=전체 결측 수(판정은 이 값으로) ·
    missing_days=앞에서 31개까지(표시용) · truncated=목록이 잘렸는가 · pending_days=오늘·미래라
    아직 판정할 수 없는 날 수."""
    today = today or utc_today()
    end_done = min(end, today)
    if end_done <= start:
        return {
            "start": start.isoformat(), "end": start.isoformat(), "days": 0, "covered": 0,
            "missing_count": 0, "missing_days": [], "truncated": False,
            "pending_days": max((end - max(start, today)).days, 0),
        }
    have = covered_days(db, cloud_account_id, start, end_done)
    missing: list[str] = []
    missing_count = 0
    d = start
    while d < end_done:
        if d not in have:
            missing_count += 1
            if len(missing) < MISSING_DAYS_LIST_LIMIT:
                missing.append(d.isoformat())
        d += dt.timedelta(days=1)
    days = (end_done - start).days
    return {
        "start": start.isoformat(), "end": end_done.isoformat(), "days": days,
        "covered": days - missing_count, "missing_count": missing_count,
        "missing_days": missing, "truncated": missing_count > len(missing),
        "pending_days": max((end - end_done).days, 0),
    }


def classify_accounts(
    db: Session, accounts: list[CloudAccount], q: CostQuery
) -> tuple[dict[int, str | None], dict[int, str]]:
    """필터 안 계정을 한 번에 분류한다. 반환: (filter_excluded{id: "currency"|None}, currency_map).
    통화 필터 제외는 **통화가 확인된 계정만** 해당한다 — 아직 행이 없어 통화를 모르는 계정을
    "통화 불일치"로 단정하지 않는다."""
    currency_map = accounts_currency_map(db, accounts)
    excluded: dict[int, str | None] = {}
    for account in accounts:
        known = currency_map.get(account.id)
        excluded[account.id] = "currency" if (q.currency and known and known != q.currency) else None
    return excluded, currency_map


def evaluable_accounts(accounts: list[CloudAccount], filter_excluded: dict[int, str | None]) -> list[CloudAccount]:
    """coverage·전망·비교 판정 대상 = 필터 안 + 실측 지원 + 통화 필터로 빠지지 않은 계정 **전부**.
    마지막 run이 실패했거나 PENDING이어도 뺀다고 "완전"해지지 않으므로 포함한다."""
    return [a for a in accounts if is_cost_supported(a.provider) and not filter_excluded.get(a.id)]


def period_coverage(
    db: Session, accounts: list[CloudAccount], start: dt.date, end: dt.date, *, today: dt.date | None = None
) -> tuple[dict[int, dict], list[str], int]:
    """대상 계정들의 coverage를 한 번씩만 계산한다. 반환: ({id: coverage}, 합집합 결측일(전체·정렬),
    합집합 결측 개수). 합집합은 "하나라도 미확인인 날"이다."""
    today = today or utc_today()
    per: dict[int, dict] = {}
    union: set[str] = set()
    for a in accounts:
        c = account_coverage(db, a.id, start, end, today=today)
        per[a.id] = c
        if c["missing_count"] and not c["truncated"]:
            union.update(c["missing_days"])
        elif c["missing_count"]:
            # 잘린 목록으로 합집합을 만들면 안 된다 — 전체 결측일을 다시 읽는다(31일 초과는 드물다).
            have = covered_days(db, a.id, start, min(end, today))
            d = start
            while d < min(end, today):
                if d not in have:
                    union.add(d.isoformat())
                d += dt.timedelta(days=1)
    return per, sorted(union), len(union)


def missing_days(db: Session, user_id: int, q: CostQuery, currency: str | None = None) -> list[str]:
    """trend()가 쓰는 결측일 — 필터 안·지원·통화 필터 밖이 아닌 계정 중 하나라도 미확인인 날(UTC).
    `currency`는 trend가 그리는 통화로, 그 통화가 아닌 계정은 대상에서 뺀다(그래프에 없는 계정의
    결측을 그래프 결측으로 찍지 않는다)."""
    accounts = owned_accounts(db, user_id, q)
    scoped = CostQuery(
        period_start=q.period_start, period_end=q.period_end, providers=q.providers,
        cloud_account_ids=q.cloud_account_ids, currency=currency or q.currency, charge_categories=q.charge_categories,
    )
    filter_excluded, _ = classify_accounts(db, accounts, scoped)
    targets = evaluable_accounts(accounts, filter_excluded)
    _, union, _ = period_coverage(db, targets, q.period_start, q.period_end)
    return union


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    return (next_month - dt.date(year, month, 1)).days


def forecast_month_end(
    db: Session, user_id: int, q: CostQuery, *, accounts: list[CloudAccount] | None = None,
    coverage_by_account: dict[int, dict] | None = None, currency_map: dict[int, str] | None = None,
) -> tuple[list[dict], dict]:
    """CF-003 — 이번 달 진행 중일 때만 낸다(§4-2). 계산식: 이번 달 **실제로 수집된 날짜**의 누적
    실측 ÷ **오늘까지의 달력 경과일수** × 이달 총일수(mtd_prorated). 수집이 안 된 날은 0원으로
    채우지 않고 그냥 합계에서 빠진다(03 §5 · QA-08 ⑤ "미수집일을 0으로 평균 내지 않는다") — 다만
    분모는 "수집된 날 수"가 아니라 "오늘이 이달 며칠째인가"다(2026-09-22 결정). 이전에는 대상
    계정 중 하나라도 이달 1일~UTC 어제를 빠짐없이 수집하지 못하면 계산 자체를 하지 않았지만,
    그러면 수집 지연이 흔한 상황에서 전망이 아예 안 뜨는 문제가 있었다 — 이제는 수집 누락이
    있어도 항상 계산하고, 어느 계정이 얼마나 빠졌는지는 forecast_status.incomplete_accounts로
    안내만 한다(계산을 막지 않는다). 반환: (forecast rows, forecast_status)."""
    today = utc_today()
    this_month_start = today.replace(day=1)
    # 전망 창은 어차피 이달 1일~어제(UTC)다. period_end가 '오늘'(어제까지 포함)이든 '오늘+1'(오늘 포함)이든
    # 근거 데이터가 같으므로 둘 다 받는다(2026-09-21 결정). 더 이르면 not_current_month, 미래면 내지 않는다.
    is_current_month = q.period_start == this_month_start and q.period_end in (today, today + dt.timedelta(days=1))
    status = {"state": "computed", "based_through": None, "required_accounts": 0, "incomplete_accounts": []}
    if not is_current_month:
        status["state"] = "not_current_month"
        return [], status
    if today.day == 1:
        status["state"] = "first_day"
        return [], status

    if accounts is None:
        accounts = owned_accounts(db, user_id, q)
        filter_excluded, currency_map = classify_accounts(db, accounts, q)
        accounts = evaluable_accounts(accounts, filter_excluded)
    if currency_map is None:
        currency_map = accounts_currency_map(db, accounts)
    status["required_accounts"] = len(accounts)
    if not accounts:
        status["state"] = "no_accounts"
        return [], status

    based_through = today - dt.timedelta(days=1)
    status["based_through"] = based_through.isoformat()
    incomplete = []
    for a in accounts:
        c = (coverage_by_account or {}).get(a.id)
        if c is None or c["start"] != this_month_start.isoformat() or c["end"] != today.isoformat():
            c = account_coverage(db, a.id, this_month_start, today, today=today)
        if c["missing_count"]:
            incomplete.append({"cloud_account_id": str(a.id), "missing_count": c["missing_count"]})
    status["incomplete_accounts"] = incomplete  # 안내용 — 더 이상 계산을 막지 않는다

    days_elapsed = today.day - 1  # 달력 기준 경과일수 — 오늘은 미완성 구간이라 분모에 넣지 않는다
    days_in_month = _days_in_month(today.year, today.month)
    rows = sum_by_currency(db, user_id, q, period_end_override=today)
    if not rows:
        # 이번 달 수집된 실측 행이 하나도 없다. 통화를 아는 계정이 있으면 그 통화로 0을 낸다(확인된 0원).
        currencies = sorted({currency_map.get(a.id) for a in accounts if currency_map.get(a.id)})
        if q.currency:
            currencies = [q.currency] if q.currency in currencies or not currencies else []
        if not currencies:
            status["state"] = "currency_unknown"
            return [], status
        rows = [(cur, Decimal("0"), False) for cur in currencies]
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
    return out, status


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


UNALLOCATED_KEY = "__unallocated__"   # service IS NULL 행 — CSP 서비스 이름과 충돌하지 않는 내부 식별자
UNALLOCATED_LABEL = "미분류"


def changes(
    db: Session, user_id: int, q: CostQuery, compare: str, dimension: str, top_n: int,
    currency_param: str | None = None,
) -> dict:
    """EXT-F12·CF-024. `comparable`은 이제 "같은 길이"만이 아니라 **비교에 필요한 정합성 전부**다 —
    같은 길이 · 완료된 기간(오늘·미래 미포함) · 두 기간 모두 대상 계정 전부 수집 확인 · 통화 하나 확정.
    하나라도 어긋나면 comparable=false이고 delta/delta_pct는 null이다. 확인된 현재·이전 합계는
    (구할 수 있으면) 그대로 준다 — 화면이 "불완전"을 붙여 따로 보여줄 수 있게. 기간을 조용히 잘라
    다른 요청으로 바꾸지 않는다. 요금 분류는 usage 고정(05 §4-5 · 비중과 같은 이유)."""
    accounts = owned_accounts(db, user_id, q)
    filter_excluded, currency_map = classify_accounts(db, accounts, q)
    targets = evaluable_accounts(accounts, filter_excluded)
    per_account_currency = {a.id: currency_map[a.id] for a in targets if a.id in currency_map}
    chosen_currency, _reason, _excluded = pick_currency(per_account_currency, currency_param or q.currency)

    days = (q.period_end - q.period_start).days
    if compare == "previous_month":
        prev_start = _shift_one_month_back(q.period_start)
        prev_end = _shift_one_month_back(q.period_end)
    else:  # previous_period
        prev_end = q.period_start
        prev_start = prev_end - dt.timedelta(days=days)
    prev_days = (prev_end - prev_start).days

    today = utc_today()
    reasons: list[str] = []
    same_length = prev_days == days
    completed_period = q.period_end <= today   # 오늘(미완성)·미래가 들어 있으면 완료된 기간 비교가 아니다
    if not same_length:
        reasons.append("LENGTH_MISMATCH")
    if not completed_period:
        reasons.append("INCOMPLETE_PERIOD")
    if not targets:
        reasons.append("NO_ACCOUNTS")
    cur_cov, _, _ = period_coverage(db, targets, q.period_start, q.period_end, today=today)
    prev_cov, _, _ = period_coverage(db, targets, prev_start, prev_end, today=today)
    current_covered = all(c["missing_count"] == 0 for c in cur_cov.values())
    previous_covered = all(c["missing_count"] == 0 for c in prev_cov.values())
    if targets and not current_covered:
        reasons.append("CURRENT_COVERAGE")
    if targets and not previous_covered:
        reasons.append("PREVIOUS_COVERAGE")
    if chosen_currency is None:
        reasons.append("NO_CURRENCY")
    comparable = not reasons
    comparability = {
        "same_length": same_length, "completed_period": completed_period,
        "current_covered": current_covered, "previous_covered": previous_covered,
        "currency": chosen_currency, "charge_category": "usage", "reasons": reasons,
        "accounts": [
            {
                "cloud_account_id": str(a.id),
                "current_missing_count": cur_cov[a.id]["missing_count"],
                "previous_missing_count": prev_cov[a.id]["missing_count"],
            }
            for a in targets if cur_cov[a.id]["missing_count"] or prev_cov[a.id]["missing_count"]
        ],
    }

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
        # service IS NULL(계정 단위 금액)은 버리지 않고 미분류 키로 합계에 넣는다(QA-09) — summary의
        # mtd_actual과 같은 범위여야 CF-024 총액이 CF-002와 어긋나지 않는다. account/provider 차원은 NULL이 없다.
        return {(UNALLOCATED_KEY if k is None else str(k)): v for k, v in query.group_by(col).all()}

    def label_of(key: str) -> str:
        return UNALLOCATED_LABEL if key == UNALLOCATED_KEY else key

    base = {
        "current": {"start": q.period_start.isoformat(), "end": q.period_end.isoformat(), "days": days},
        "previous": {"start": prev_start.isoformat(), "end": prev_end.isoformat(), "days": prev_days},
        "comparable": comparable, "comparability": comparability, "currency": chosen_currency,
        "charge_category": "usage",
    }

    current_sums = dim_sums(q.period_start, q.period_end) if chosen_currency else {}
    current_total = sum(current_sums.values(), Decimal("0"))
    previous_sums = dim_sums(prev_start, prev_end) if chosen_currency else {}
    previous_total = sum(previous_sums.values(), Decimal("0"))

    if not comparable:
        base.update({
            "totals": {
                "current": money(current_total) if chosen_currency else None,
                "previous": money(previous_total) if chosen_currency else None,
                "delta": None, "delta_pct": None,
            },
            "increases": [], "decreases": [], "new_items": [],
        })
        return base

    delta = current_total - previous_total
    delta_pct = str((delta / previous_total * 100).quantize(Decimal("0.1"))) if previous_total > 0 else None

    increases, decreases, new_items = [], [], []
    for key in set(current_sums) | set(previous_sums):
        cur = current_sums.get(key)
        prev = previous_sums.get(key)
        if prev is None:
            new_items.append({"key": key, "label": label_of(key), "current": money(cur), "previous": None})
            continue
        d_amount = (cur or Decimal("0")) - prev
        d_pct = str((d_amount / prev * 100).quantize(Decimal("0.1"))) if prev > 0 else None
        item = {
            "key": key, "label": label_of(key), "current": money(cur or Decimal("0")), "previous": money(prev),
            "delta": money(d_amount), "delta_pct": d_pct, "is_new": False,
        }
        (increases if d_amount > 0 else decreases).append(item)

    increases.sort(key=lambda i: Decimal(i["delta"]), reverse=True)
    decreases.sort(key=lambda i: Decimal(i["delta"]))

    base.update({
        "totals": {
            "current": money(current_total), "previous": money(previous_total),
            "delta": money(delta), "delta_pct": delta_pct,
        },
        "increases": increases[:top_n], "decreases": decreases[:top_n], "new_items": new_items[:top_n],
    })
    return base


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


DATA_STATUSES = ("CONNECTED_OK", "CONNECTED_EMPTY", "CONNECTED_PARTIAL")


def _exclusion_reason(account: CloudAccount, cap_status: str, filter_excluded: str | None, coverage: dict | None, actual: Decimal | None) -> str | None:
    """합계(mtd_actual)에 이 계정이 기여하지 못한 이유 — 계정당 하나. 우선순위: 미지원 > 통화 필터 >
    (이 기간에 확인된 날도 행도 없을 때) capability 상태 또는 PERIOD_NOT_COVERED. 행이 있으면
    마지막 run이 실패여도 합계에 들어 있으므로 제외가 아니다(부족분은 coverage가 말한다)."""
    if not is_cost_supported(account.provider):
        return "UNSUPPORTED"
    if filter_excluded == "currency":
        return "CURRENCY_FILTERED"
    if actual is not None:
        return None
    if coverage and coverage["covered"] > 0:
        return None  # 확인된 날이 있는데 행이 없다 = 확인된 0원(합계에 0으로 포함된 것과 같다)
    return cap_status if cap_status not in DATA_STATUSES else "PERIOD_NOT_COVERED"


def summary(db: Session, user_id: int, q: CostQuery) -> dict:
    from app.cost.capability import account_capability

    accounts = owned_accounts(db, user_id, q)
    filter_excluded, currency_map = classify_accounts(db, accounts, q)
    targets = evaluable_accounts(accounts, filter_excluded)
    coverage_by_account, union_missing, union_missing_count = period_coverage(db, targets, q.period_start, q.period_end)

    mtd_actual_rows = sum_by_currency(db, user_id, q)
    mtd_net_rows = sum_by_currency(
        db, user_id, q, charge_categories=["usage", "credit", "refund", "tax", "other"]
    )
    list_price_rows, missing_count = list_price_monthly(db, user_id, q)
    forecast_rows, forecast_status = forecast_month_end(
        db, user_id, q, accounts=targets, coverage_by_account=coverage_by_account, currency_map=currency_map
    )

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
        "forecast_status": forecast_status,
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

        actual_amount, actual_currency = actual_by_account.get(account.id, (None, None))
        coverage = coverage_by_account.get(account.id)  # 미지원·통화 필터 제외 계정은 None
        reason = _exclusion_reason(account, cap["status"], filter_excluded.get(account.id), coverage, actual_amount)
        if reason:
            excluded_count += 1
            excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
        rc = resource_counts.get(account.id, {"count": 0, "synced_at": None})

        accounts_out.append({
            "cloud_account_id": str(account.id), "provider": account.provider,
            "account_label": account.account_label, "team_id": str(account.team_id) if account.team_id else None,
            "status": cap["status"], "as_of": cap["as_of"], "ingestion_running": cap["ingestion_running"],
            "currency": actual_currency or currency_map.get(account.id) or cap["currency"],
            "actual": money(actual_amount), "list_price_estimate": money(list_price_by_account.get(account.id)),
            "resource_count": rc["count"], "resources_synced_at": rc["synced_at"],
            "is_estimated": actual_amount is not None,
            "filter_excluded": filter_excluded.get(account.id),
            "coverage": coverage,
        })

    warnings = []
    if union_missing:
        warnings.append({
            "code": "PARTIAL_PERIOD",
            "message": f"{union_missing[0]} ~ {union_missing[-1]} 구간 중 일부가 수집되지 않았습니다.",
            "missing_days": union_missing,
            "missing_count": union_missing_count,
            "accounts": [
                {"cloud_account_id": str(a.id), "missing_count": coverage_by_account[a.id]["missing_count"]}
                for a in targets if coverage_by_account[a.id]["missing_count"]
            ],
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
    filter_excluded, _ = classify_accounts(db, accounts, q)
    coverage_by_account, _, _ = period_coverage(db, evaluable_accounts(accounts, filter_excluded), q.period_start, q.period_end)
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
        # 조회 기간(요청 period_start/end) 기준 계정별 수집 확인 — 지원·통화 필터 안 계정만. 미지원은 None.
        coverage = coverage_by_account.get(account.id)

        items.append({
            "cloud_account_id": account.id, "provider": account.provider,
            "status": cap["status"], "as_of": cap["as_of"], "ingestion_running": cap["ingestion_running"],
            "last_success_at": last_success.finished_at if last_success else None,
            "last_attempt_at": last_attempt.requested_at if last_attempt else None,
            "last_error_code": cap["last_error_code"],
            "next_manual_allowed_at": next_allowed,
            "covered_through": covered_through,
            "missing_days": coverage["missing_days"] if coverage else [],
            "missing_count": coverage["missing_count"] if coverage else 0,
            "coverage": coverage,
        })
    return items
