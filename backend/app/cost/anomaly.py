"""급증 탐지 — 규칙 기반, 저장 테이블 없음(PR 8 · docs/비용_개발문서/05_API계약.md §7-1 · 08 §7-1).

**DB만 읽는다**(ADR-040). 요청(또는 수집 훅)마다 `cloud_account_costs`에서 규칙을 적용해 계산하고,
검토 상태는 `cost_review_items`에서 붙여 온다. 결과를 저장하지 않으므로 규칙이 바뀌어도 과거 판정이
거짓이 되지 않는다.

규칙(문서 확정): 계정 × 서비스 × 완료된 날 · 직전 7일 평균 대비 (차액 ≥ 최소 차액) AND (증가율 ≥ 50%)
· 이력 12일 이상 · 최근 3일 제외 · 요금분류 `usage` 고정(순액으로 판정하면 크레딧이 들어온 날이
급증으로 잡힌다) · 라벨은 항상 "원인 확인 필요"(확정 13).

문서가 비워 둔 칸 — 2026-09-19 승현 결정:
- 기준선 7일은 **7일 전부 수집 확인**됐을 때만 판정한다. 하루라도 미수집이면 그 판정일은 보류하고
  `held[]`에 사유를 적는다. 미수집일을 0으로 넣지 않는다(평균이 낮아져 가짜 급증이 된다).
- 수집 확인된 $0 날은 0으로 센다(정상). 정의는 coverage.py 한 곳.
- 이력 12일 = **판정일 이하**의 수집 확인 날 수. 이후에 쌓인 날은 과거 판정의 이력에 넣지 않는다.
- **기준선 0 예외**: 증가율을 계산할 수 없으므로 비율식을 적용하지 않고 `delta ≥ 최소 차액`만으로
  탐지한다. `delta_pct`는 `null`(무한대·0%가 아니다 — 화면·AI는 "신규 비용 발생"으로 읽는다).
- 최소 차액은 통화별 상수다(`$5`는 USD 기준). 값이 없는 통화(KRW 등)는 판정하지 않고
  `unsupported_currency[]`로 응답에 보인다 — **USD만 판정하는 것은 중간 상태**이고 KRW 정책은 미결.
- 날짜 경계는 UTC(coverage.utc_today).
- "최근 3일 제외" = 오늘·어제·그제·그끄제 제외 → 판정 가능한 마지막 날 = 오늘−4.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import cast, Date, func
from sqlalchemy.orm import Session

from app.cost import is_cost_supported
from app.cost.coverage import analysis_ready_days_since_first, covered_days_since_first, utc_today
from app.cost.query import accounts_currency_map, money
from app.models import CloudAccount, CloudAccountCost, CostReviewItem, Credential, ProvisioningJob, ServiceCatalog

BASELINE_DAYS = 7
MIN_INCREASE_PCT = 50
MIN_HISTORY_DAYS = 12
EXCLUDE_RECENT_DAYS = 3
CHARGE_CATEGORIES = ["usage"]
LABEL = "원인 확인 필요"
SOURCE_TYPE = "cost_anomaly"

# 통화별 최소 차액. KRW 값은 미결(교차검토 Q3) — 정해지면 여기 한 줄만 늘린다. 환율 환산으로 만들지
# 않는다(환율이 바뀌면 같은 비용의 경보 여부가 날마다 달라진다, ADR-043).
MIN_DELTA_BY_CURRENCY: dict[str, Decimal] = {"USD": Decimal("5")}

HELD_BASELINE_INCOMPLETE = "baseline_incomplete"
HELD_DAY_NOT_COLLECTED = "day_not_collected"
# 금액은 관측됐는데 완전성 근거가 없어 판정하지 못한 날 — "수집이 안 됐다"와 다르다(A-2).
HELD_COVERAGE_UNVERIFIED = "coverage_unverified"


def rule_dict(currency: str = "USD") -> dict:
    return {
        "baseline_days": BASELINE_DAYS,
        "min_delta_amount": money(MIN_DELTA_BY_CURRENCY.get(currency)),
        "min_increase_pct": MIN_INCREASE_PCT,
        "min_history_days": MIN_HISTORY_DAYS,
        "exclude_recent_days": EXCLUDE_RECENT_DAYS,
        "currency": currency,
        "charge_category": list(CHARGE_CATEGORIES),
    }


def eligible_end(today: dt.date) -> dt.date:
    """판정 가능한 날의 제외 경계 — 오늘−3 (즉 마지막 판정일은 오늘−4)."""
    return today - dt.timedelta(days=EXCLUDE_RECENT_DAYS)


def source_key(cloud_account_id: int, service: str, day: dt.date) -> str:
    return f"{cloud_account_id}:{service}:{day.isoformat()}"


def parse_source_key(key: str) -> tuple[int, str, dt.date]:
    """`{cloud_account_id}:{service}:{YYYY-MM-DD}` — 서비스 이름에 ':'가 들어갈 수 있어 양끝만 자른다."""
    first, _, rest = key.partition(":")
    service, _, day = rest.rpartition(":")
    if not first or not service or not day:
        raise ValueError("source_key 형식이 아닙니다.")
    return int(first), service, dt.date.fromisoformat(day)


@dataclass
class AnomalyItem:
    cloud_account_id: int
    provider: str
    service: str
    date: dt.date
    currency: str
    amount: Decimal
    baseline_amount: Decimal
    delta: Decimal
    delta_pct: Decimal | None  # 기준선 0이면 None

    @property
    def source_key(self) -> str:
        return source_key(self.cloud_account_id, self.service, self.date)


@dataclass
class AccountEvaluation:
    account: CloudAccount
    currency: str | None
    items: list[AnomalyItem] = field(default_factory=list)
    insufficient_history: dict | None = None  # {days_available, days_required}
    held: list[dict] = field(default_factory=list)  # [{date, reason}]
    unsupported_currency: bool = False
    is_sample_data: bool = False


def _usage_by_service_day(
    db: Session, account: CloudAccount, currency: str, start: dt.date, end: dt.date
) -> tuple[dict[tuple[str, dt.date], Decimal], bool]:
    rows = (
        db.query(
            CloudAccountCost.service, CloudAccountCost.period_start,
            func.sum(CloudAccountCost.amount), func.bool_or(CloudAccountCost.source.like("seed%")),
        )
        .filter(
            CloudAccountCost.cloud_account_id == account.id,
            CloudAccountCost.currency == currency,
            CloudAccountCost.charge_category.in_(CHARGE_CATEGORIES),
            CloudAccountCost.period_start >= start,
            CloudAccountCost.period_start < end,
        )
        .group_by(CloudAccountCost.service, CloudAccountCost.period_start)
        .all()
    )
    out: dict[tuple[str, dt.date], Decimal] = {}
    sample = False
    for service, day, total, is_seed in rows:
        out[(service or "", day)] = total
        sample = sample or bool(is_seed)
    return out, sample


def _pct(delta: Decimal, baseline: Decimal) -> Decimal:
    return (delta / baseline * Decimal(100)).quantize(Decimal("0.1"))


def evaluate_account(
    db: Session,
    account: CloudAccount,
    eval_start: dt.date,
    eval_end: dt.date,
    *,
    currency: str | None = None,
) -> AccountEvaluation:
    """계정 하나를 [eval_start, eval_end) 판정일에 대해 평가한다. 호출부가 eval_end를 eligible_end 이하로
    잘라 준다. 기준선에 필요한 직전 7일은 여기서 따로 읽는다 — 조회 기간이 짧아도 이력 부족이 되지
    않는다."""
    if currency is None:
        currency = accounts_currency_map(db, [account]).get(account.id)
    result = AccountEvaluation(account=account, currency=currency)
    if eval_end <= eval_start:
        return result
    if currency is None:
        # 수집된 행이 하나도 없다 — 이력 0일
        result.insufficient_history = {"days_available": 0, "days_required": MIN_HISTORY_DAYS}
        return result
    if currency not in MIN_DELTA_BY_CURRENCY:
        result.unsupported_currency = True
        return result
    min_delta = MIN_DELTA_BY_CURRENCY[currency]

    # 판정에는 **근거가 있는 날**만 쓴다(A-2). 모든 날짜에 행이 있어도 완전성 근거가 없으면
    # (observed_only) 기준선·이력이 채워지지 않아 held로 남는다 — 가짜 급증을 만들지 않기 위해서다.
    covered_all = analysis_ready_days_since_first(db, account.id, eval_end)
    covered_sorted = sorted(covered_all)
    # 관측된 날(행이 있거나 근거가 약해도 들어온 날) — "수집이 안 됐다"와 "근거가 없다"를 가르는 데 쓴다.
    observed_all = covered_days_since_first(db, account.id, eval_end)
    observed_sorted = sorted(observed_all)
    usage, sample = _usage_by_service_day(db, account, currency, eval_start - dt.timedelta(days=BASELINE_DAYS), eval_end)
    result.is_sample_data = sample
    services = sorted({svc for svc, _ in usage})

    def history_through(day: dt.date) -> int:
        # 판정일 이하의 **판정 가능한** 날 수 — 이후 날짜는 세지 않는다
        import bisect
        return bisect.bisect_right(covered_sorted, day)

    def observed_through(day: dt.date) -> int:
        import bisect
        return bisect.bisect_right(observed_sorted, day)

    last_days_available = 0
    d = eval_start
    while d < eval_end:
        days_available = history_through(d)
        last_days_available = days_available
        if days_available < MIN_HISTORY_DAYS:
            # 이력이 모자란 이유가 "아직 안 쌓였다"인지 "쌓였는데 근거가 없다"인지 구분한다 —
            # 관측 이력은 충분한데 판정 가능한 날만 모자라면 그건 이력 부족이 아니라 근거 부족이다.
            if observed_through(d) >= MIN_HISTORY_DAYS:
                result.held.append({"date": d, "reason": HELD_COVERAGE_UNVERIFIED})
            d += dt.timedelta(days=1)
            continue
        if d not in covered_all:
            reason = HELD_COVERAGE_UNVERIFIED if d in observed_all else HELD_DAY_NOT_COLLECTED
            result.held.append({"date": d, "reason": reason})
            d += dt.timedelta(days=1)
            continue
        window = [d - dt.timedelta(days=i) for i in range(BASELINE_DAYS, 0, -1)]
        if any(w not in covered_all for w in window):
            unverified_window = all(w in observed_all for w in window)   # 관측은 다 됐는데 근거만 없다
            result.held.append({"date": d,
                                "reason": HELD_COVERAGE_UNVERIFIED if unverified_window else HELD_BASELINE_INCOMPLETE})
            d += dt.timedelta(days=1)
            continue
        for svc in services:
            amount = usage.get((svc, d), Decimal("0"))
            baseline = sum((usage.get((svc, w), Decimal("0")) for w in window), Decimal("0")) / Decimal(BASELINE_DAYS)
            delta = amount - baseline
            if delta < min_delta:
                continue
            if baseline == 0:
                pct = None  # 기준선 0 예외 — 비율식을 적용하지 않는다
            else:
                pct = _pct(delta, baseline)
                if pct < MIN_INCREASE_PCT:
                    continue
            result.items.append(AnomalyItem(
                cloud_account_id=account.id, provider=account.provider, service=svc, date=d, currency=currency,
                amount=amount.quantize(Decimal("0.000001")), baseline_amount=baseline.quantize(Decimal("0.000001")),
                delta=delta.quantize(Decimal("0.000001")), delta_pct=pct,
            ))
        d += dt.timedelta(days=1)

    # 이력 부족은 **관측 이력까지 모자랄 때만** 말한다 — 근거 부족을 "이력 0일"이라고 설명하면
    # 사용자가 "아직 데이터가 없구나"로 잘못 읽는다(A-2 보완).
    if not result.items and not result.held and last_days_available < MIN_HISTORY_DAYS:
        result.insufficient_history = {"days_available": last_days_available, "days_required": MIN_HISTORY_DAYS}
    return result


def evaluate_account_all_stored(db: Session, account: CloudAccount, today: dt.date | None = None) -> AccountEvaluation:
    """자동 탐지용 — 저장된 첫 날부터 판정 가능한 마지막 날까지 전부 평가한다. 결과를 저장하지 않으므로
    "이미 본 날"을 구분할 수 없고, 알림 중복은 cost_review_items UNIQUE가 막는다. 그래서 창을 두지 않고
    매번 전부 본다 — 장기 중단 뒤에도 빠짐없이 따라잡기 위해서다(2026-09-19 결정)."""
    today = today or utc_today()
    end = eligible_end(today)
    # 판정 가능한 날이 하나도 없어도, **관측된 날이 있으면** 평가를 돌린다 — 그래야 "근거 부족"이라는
    # 사유가 held로 남는다. 그냥 건너뛰면 화면이 "이력 0일"로만 보여 사용자가 데이터가 없는 줄 안다.
    covered = analysis_ready_days_since_first(db, account.id, end)
    observed = covered_days_since_first(db, account.id, end)
    start_from = covered or observed
    if not start_from:
        return AccountEvaluation(account=account, currency=None)
    return evaluate_account(db, account, min(start_from), end)


# --- 조회 응답 조립 ---------------------------------------------------------------------------


def _related_changes(db: Session, user_id: int, account_ids: list[int], start: dt.date, end: dt.date) -> dict[tuple[int, dt.date], list[dict]]:
    """같은 날 끝난(또는 만들어진) 프로비저닝 기록 — "관련 변경 후보"이지 원인이 아니다."""
    if not account_ids:
        return {}
    ts = func.coalesce(ProvisioningJob.finished_at, ProvisioningJob.created_at)
    rows = (
        db.query(ProvisioningJob.id, Credential.cloud_account_id, ServiceCatalog.service_code, ProvisioningJob.finished_at, ProvisioningJob.created_at)
        .join(Credential, Credential.id == ProvisioningJob.credential_id)
        .join(ServiceCatalog, ServiceCatalog.id == ProvisioningJob.service_catalog_id)
        .filter(
            ProvisioningJob.user_id == user_id,
            Credential.cloud_account_id.in_(account_ids),
            cast(ts, Date) >= start,
            cast(ts, Date) < end,
        )
        .all()
    )
    out: dict[tuple[int, dt.date], list[dict]] = {}
    for job_id, account_id, service_code, finished_at, created_at in rows:
        when = finished_at or created_at
        out.setdefault((account_id, when.date()), []).append(
            {"type": "provisioning_job", "id": job_id, "service_code": service_code, "finished_at": finished_at}
        )
    return out


def review_map(db: Session, user_id: int, keys: list[str]) -> dict[str, CostReviewItem]:
    if not keys:
        return {}
    rows = (
        db.query(CostReviewItem)
        .filter(CostReviewItem.user_id == user_id, CostReviewItem.source_type == SOURCE_TYPE, CostReviewItem.source_key.in_(keys))
        .all()
    )
    return {r.source_key: r for r in rows}


def detect_anomalies(
    db: Session, user_id: int, accounts: list[CloudAccount], period_start: dt.date, period_end: dt.date,
    *, status: str = "open", today: dt.date | None = None,
) -> dict:
    """`GET /cost-anomalies` 본체. 반환 dict는 schemas.cost_review.AnomaliesData 모양(날짜는 date 그대로)."""
    today = today or utc_today()
    eval_end = min(period_end, eligible_end(today))
    currency_of = accounts_currency_map(db, accounts)

    items_out: list[dict] = []
    insufficient: list[dict] = []
    held: list[dict] = []
    unsupported: list[dict] = []
    evaluations: list[AccountEvaluation] = []
    for account in accounts:
        if not is_cost_supported(account.provider):
            continue  # 수집 미구현 CSP는 capabilities가 UNSUPPORTED로 이미 보여준다
        ev = evaluate_account(db, account, period_start, eval_end, currency=currency_of.get(account.id))
        evaluations.append(ev)
        if ev.unsupported_currency:
            unsupported.append({"cloud_account_id": account.id, "provider": account.provider, "currency": ev.currency})
        if ev.insufficient_history:
            insufficient.append({"cloud_account_id": account.id, **ev.insufficient_history})
        if ev.held:
            held.append({"cloud_account_id": account.id, "days": ev.held})

    all_items = [it for ev in evaluations for it in ev.items]
    reviews = review_map(db, user_id, [it.source_key for it in all_items])
    related = _related_changes(db, user_id, [ev.account.id for ev in evaluations], period_start, eval_end) if all_items else {}
    sample_accounts = {ev.account.id for ev in evaluations if ev.is_sample_data}

    for it in sorted(all_items, key=lambda x: (x.date, x.cloud_account_id, x.service), reverse=True):
        review = reviews.get(it.source_key)
        review_status = review.status if review else None
        if status == "open" and review_status == "resolved":
            continue
        if status == "resolved" and review_status != "resolved":
            continue
        items_out.append({
            "source_key": it.source_key,
            "cloud_account_id": it.cloud_account_id, "provider": it.provider, "service": it.service, "date": it.date,
            "currency": it.currency, "amount": money(it.amount), "baseline_amount": money(it.baseline_amount),
            "delta": money(it.delta), "delta_pct": None if it.delta_pct is None else str(it.delta_pct),
            "review": {"item_id": review.id, "status": review.status, "resolution": review.resolution} if review else None,
            "related_changes": related.get((it.cloud_account_id, it.date), []),
            "label": LABEL,
            "is_sample_data": it.cloud_account_id in sample_accounts,
        })

    return {
        "rule": rule_dict("USD"),
        "items": items_out,
        "insufficient_history": insufficient,
        "held": held,
        "unsupported_currency": unsupported,
        "total": len(items_out),
        "pagination": None,
    }
