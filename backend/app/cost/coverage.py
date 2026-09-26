"""수집 확인(coverage) — "이 계정의 이 날은 무엇을 근거로 확인됐는가"를 한 곳에서 정의한다.

축이 **둘**이다. 섞으면 "행이 있으니 판정해도 된다"는 잘못된 결론이 나온다(2026-09-23 A-2).

| 축 | 뜻 | 쓰는 곳 |
|---|---|---|
| **관측(covered)** | 그 날 금액이 실제로 들어왔거나(행), 요청 범위를 확인 근거로 인정할 수 있다 | 금액 표시·결측 안내 |
| **분석 가능(analysis-ready)** | 그 날의 **완전성 근거**가 있다 | 전망·기간 비교·예산 임계·급증 판정 |

근거는 수집 run마다 `coverage_basis`로 기록한다.

- `complete_range` — 요청 범위를 확인 근거로 인정한다(**기존 AWS 정책 그대로**). 최종 청구 확정을
  뜻하지 않는다 — 정정은 여전히 올 수 있다.
- `observed_only` — 받은 금액만 관측했을 뿐 "어디까지 왔는지"를 CSP가 알려 주지 않는다. 모든 날짜에
  행이 있어도 **일부 서비스가 미도착일 수 있으므로 판정하지 않는다**(초기 Azure·GCP).

**날짜별 근거 = 그 날을 덮는 성공 run 중 가장 나중에 저장한 run의 basis**다. 저장은 범위 교체
(`ingest.replace_cost_rows`)이고 행 교체와 run 상태·`finished_at` 기록이 **같은 트랜잭션**에서
커밋되므로(`routers/costs.py`·`cost/scheduler.py`), `finished_at`(동률이면 `id`) 순서가 곧 저장 순서다.
계정당 advisory 락 + "진행 중이면 거부"가 동시 실행을 막아 순서가 뒤집히지 않는다. 실패·
partial_success run은 행을 쓰지 않으므로 근거도 바꾸지 않는다(status='success'만 본다).

**레거시 호환**: `coverage_basis IS NULL`(컬럼 추가 이전 run)과 "run 없이 행만 있는" 옛 데이터는
**provider가 aws일 때만** `complete_range`로 읽는다. 비AWS의 NULL·알 수 없는 값은 `observed_only`다 —
모르는 값이 판정 가능으로 확대되지 않게 한다.

정의: 날 d가 **관측됨** = (d에 행이 있다) OR (d를 덮는 최신 성공 run의 basis가 `complete_range`).
$0인 날은 행이 안 생기므로 행 유무만 보면 정상 0원 계정이 영원히 결측이 된다 — 그래서 run 범위를
함께 보되, 그 인정 범위를 basis가 정한다.

날짜 경계는 UTC다(2026-09-19 결정).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.models import CloudAccount, CloudAccountCost, CostIngestionRun


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


BASIS_COMPLETE_RANGE = "complete_range"
BASIS_OBSERVED_ONLY = "observed_only"
ALLOWED_BASES = (BASIS_COMPLETE_RANGE, BASIS_OBSERVED_ONLY)

# 레거시(NULL·미기록)를 강한 근거로 읽어 주는 provider — 기존 정책을 그대로 유지하는 AWS만.
LEGACY_COMPLETE_RANGE_PROVIDERS = frozenset({"aws"})


def resolve_basis(raw: str | None, provider: str) -> str:
    """run에 기록된 값(또는 없음)을 실제로 적용할 근거로 바꾼다. 모르는 값은 절대 넓히지 않는다."""
    if raw in ALLOWED_BASES:
        return raw
    if raw is None and provider in LEGACY_COMPLETE_RANGE_PROVIDERS:
        return BASIS_COMPLETE_RANGE
    return BASIS_OBSERVED_ONLY


def _provider_of(db: Session, cloud_account_id: int) -> str:
    provider = (
        db.query(CloudAccount.provider).filter(CloudAccount.id == cloud_account_id).scalar()
    )
    return provider or ""


def day_basis_map(db: Session, cloud_account_id: int, start: dt.date, end: dt.date) -> dict[dt.date, str]:
    """[start, end) 중 **관측된** 날짜 → 그 날의 근거(`complete_range` | `observed_only`).

    저장 순서대로 run을 적용한다 — 나중 run이 이전 run의 근거를 덮는다. `observed_only` run이
    덮은 구간에서 행이 사라진 날은 관측에서도 빠진다(범위 교체가 행을 지웠다는 뜻이므로)."""
    if end <= start:
        return {}
    provider = _provider_of(db, cloud_account_id)
    row_days = {
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
        db.query(
            CostIngestionRun.period_start, CostIngestionRun.period_end, CostIngestionRun.coverage_basis
        )
        .filter(
            CostIngestionRun.cloud_account_id == cloud_account_id,
            CostIngestionRun.status == "success",
            CostIngestionRun.period_start < end,
            CostIngestionRun.period_end > start,
        )
        # 저장 순서 = 커밋 순서. finished_at이 같으면 나중에 만들어진 run(id 큰 쪽)이 나중 저장이다.
        .order_by(CostIngestionRun.finished_at.asc().nullslast(), CostIngestionRun.id.asc())
        .all()
    )

    out: dict[dt.date, str] = {}
    for run_start, run_end, raw_basis in runs:
        basis = resolve_basis(raw_basis, provider)
        d = max(run_start, start)
        stop = min(run_end, end)
        while d < stop:
            if basis == BASIS_COMPLETE_RANGE:
                out[d] = BASIS_COMPLETE_RANGE
            elif d in row_days:
                out[d] = BASIS_OBSERVED_ONLY
            else:
                out.pop(d, None)   # 이 run이 지운 구간이고 새 행도 없다 → 관측 아님
            d += dt.timedelta(days=1)

    # run이 닿지 않은 날에 행만 있는 옛 데이터(시드 등) — provider별 레거시 규칙으로 읽는다.
    legacy = resolve_basis(None, provider)
    for d in row_days:
        out.setdefault(d, legacy)
    return out


def covered_days(db: Session, cloud_account_id: int, start: dt.date, end: dt.date) -> set[dt.date]:
    """[start, end) 안에서 **관측된** 날짜 집합(금액 표시·결측 안내용)."""
    return set(day_basis_map(db, cloud_account_id, start, end))


def analysis_ready_days(db: Session, cloud_account_id: int, start: dt.date, end: dt.date) -> set[dt.date]:
    """[start, end) 안에서 **판정에 쓸 수 있는**(완전성 근거가 있는) 날짜 집합."""
    return {d for d, basis in day_basis_map(db, cloud_account_id, start, end).items()
            if basis == BASIS_COMPLETE_RANGE}


def period_basis(db: Session, cloud_account_id: int, start: dt.date, end: dt.date) -> str | None:
    """이 기간의 근거 요약. 관측된 날이 없으면 `None`, **전부** complete_range면 `complete_range`,
    하나라도 observed_only면 `observed_only`(기간 전체를 최신 run 하나로 설명하지 않는다)."""
    bases = set(day_basis_map(db, cloud_account_id, start, end).values())
    if not bases:
        return None
    return BASIS_COMPLETE_RANGE if bases == {BASIS_COMPLETE_RANGE} else BASIS_OBSERVED_ONLY


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


def _first_day(db: Session, cloud_account_id: int) -> dt.date | None:
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
    return min(candidates) if candidates else None


def covered_days_since_first(db: Session, cloud_account_id: int, end: dt.date) -> set[dt.date]:
    """계정의 첫 관측일부터 `end`(제외)까지의 **관측된** 날짜 집합."""
    first = _first_day(db, cloud_account_id)
    return covered_days(db, cloud_account_id, first, end) if first else set()


def analysis_ready_days_since_first(db: Session, cloud_account_id: int, end: dt.date) -> set[dt.date]:
    """같은 범위에서 **판정에 쓸 수 있는** 날짜 집합 — 급증 판정이 기준선·이력을 셀 때 쓴다."""
    first = _first_day(db, cloud_account_id)
    return analysis_ready_days(db, cloud_account_id, first, end) if first else set()


def eligible_end_for_today() -> dt.date:
    """급증 판정 가능한 날의 제외 경계(오늘 UTC 기준) — anomaly.eligible_end의 편의 함수."""
    from app.cost.anomaly import eligible_end

    return eligible_end(utc_today())
