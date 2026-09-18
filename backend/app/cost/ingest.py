"""`cloud_account_costs` 저장 — 범위 교체(UPSERT 아님, docs/비용_개발문서/06_DB변경.md §3-3).

같은 (cloud_account_id, period_start 범위, source)를 한 트랜잭션에서 delete → insert 한다.
CSP 응답을 전부 받은 뒤에만 호출한다 — 지우고 받으면 중간 실패 때 기존 데이터가 사라진다.

동시 실행 방지는 PostgreSQL advisory 트랜잭션 락으로 한다(pg_advisory_lock 세션 락이 아니다 —
프로세스가 죽어도 잠금이 남지 않아야 하므로). 락은 이 delete+insert 임계 구간에만 건다 — 그
앞의 "이미 진행 중" 1차 판정은 `cost_ingestion_runs`의 pending/running 행 조회로 한다
(라우터, `08_백엔드_구현가이드.md` §4-5).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cost.base import CostRow
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun

# 'COST'를 임의로 눌러 담은 advisory lock 네임스페이스. 저장소에서 advisory lock을 쓰는
# 곳이 이거 하나라 다른 기능과 충돌할 일은 없지만, 값을 고정해 문서와 코드가 어긋나지 않게 한다.
LOCK_NAMESPACE = 0xC057


class AccountLockedError(Exception):
    """다른 프로세스가 같은 계정의 비용 수집(범위 교체)을 이미 진행 중이다."""


def try_lock_account(db: Session, cloud_account_id: int) -> bool:
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": LOCK_NAMESPACE, "key": cloud_account_id},
        ).scalar()
    )


def replace_cost_rows(
    db: Session,
    account: CloudAccount,
    run: CostIngestionRun,
    rows: list[CostRow],
    source: str,
) -> int:
    """`run.period_start`~`run.period_end` 범위, `source`가 같은 기존 행을 전부 지우고
    `rows`로 다시 채운다. 락을 못 잡으면 `AccountLockedError`를 올리고 아무것도 지우지 않는다."""
    if not try_lock_account(db, account.id):
        raise AccountLockedError()

    as_of = dt.datetime.now(dt.timezone.utc)

    db.query(CloudAccountCost).filter(
        CloudAccountCost.cloud_account_id == account.id,
        CloudAccountCost.period_start >= run.period_start,
        CloudAccountCost.period_start < run.period_end,
        CloudAccountCost.source == source,
    ).delete(synchronize_session=False)

    for row in rows:
        db.add(
            CloudAccountCost(
                cloud_account_id=account.id,
                provider=account.provider,
                charge_category=row.charge_category,
                service=row.service,
                amount=row.amount,
                currency=row.currency,
                period_start=row.period_start,
                period_end=row.period_end,
                is_estimated=row.is_estimated,
                as_of=as_of,
                source=source,
                source_record_key=row.source_record_key,
                metadata_json=row.metadata or None,
            )
        )

    return len(rows)
