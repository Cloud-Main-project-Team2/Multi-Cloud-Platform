"""PR 4 — advisory lock 동시 실행 방지 검증.

`pg_try_advisory_xact_lock`은 세션(=DB 커넥션)에 묶인다. `db_session` 픽스처 하나만 쓰면 같은
트랜잭션이라 애초에 경합이 생기지 않으므로, 독립된 커넥션 2개로 실제 잠금 경합을 재현한다.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cost.ingest import AccountLockedError, replace_cost_rows, try_lock_account
from app.models import CloudAccount, CostIngestionRun


def _database_url() -> str:
    """`db_session`(conftest)이 실제로 붙는 `mcp_db_test`를 가리킨다 — 기본 DATABASE_URL(운영/개발
    DB인 mcp_db)로 연결하면 advisory lock이 **다른 데이터베이스**에 걸려 전혀 경합하지 않는다
    (Postgres advisory lock은 데이터베이스 단위로 분리된다 — 실제로 이 버그로 테스트가 조용히
    통과할 뻔한 것을 재현 중 발견했다)."""
    base = os.environ.get("DATABASE_URL", "postgresql+psycopg2://mcp_user:change_me@db:5432/mcp_db")
    prefix, _, _ = base.rpartition("/")
    return f"{prefix}/mcp_db_test"


@pytest.fixture()
def second_connection():
    """`db_session`(conftest)과는 별도인 두 번째 실제 DB 커넥션 — advisory lock 경합용."""
    engine = create_engine(_database_url(), future=True)
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_second_lock_attempt_fails_while_first_holds_it(second_connection):
    account_id = 999001  # advisory lock은 임의 정수 키만 있으면 되고 실제 행이 필요 없다

    assert try_lock_account(second_connection, account_id) is True

    # db_session(conftest)의 커넥션에서 같은 계정 잠금을 시도 — 이미 잡혀 있으므로 실패해야 한다.
    engine2 = create_engine(_database_url(), future=True)
    third_session = sessionmaker(bind=engine2, future=True)()
    try:
        assert try_lock_account(third_session, account_id) is False
    finally:
        third_session.rollback()
        third_session.close()
        engine2.dispose()

    second_connection.rollback()  # 트랜잭션 종료 -> xact lock 자동 해제(프로세스가 죽어도 마찬가지)

    engine3 = create_engine(_database_url(), future=True)
    fourth_session = sessionmaker(bind=engine3, future=True)()
    try:
        assert try_lock_account(fourth_session, account_id) is True
    finally:
        fourth_session.rollback()
        fourth_session.close()
        engine3.dispose()


def test_lock_is_per_account_not_global(second_connection):
    assert try_lock_account(second_connection, 999002) is True

    engine2 = create_engine(_database_url(), future=True)
    other_session = sessionmaker(bind=engine2, future=True)()
    try:
        # 다른 계정 id — 경합이 없어야 한다
        assert try_lock_account(other_session, 999003) is True
    finally:
        other_session.rollback()
        other_session.close()
        engine2.dispose()


def test_replace_cost_rows_raises_account_locked_error_when_held_elsewhere(
    second_connection, db_session, make_user
):
    user = make_user()
    account = CloudAccount(user_id=user.id, provider="aws", external_account_id="111122223333")
    db_session.add(account)
    db_session.flush()

    # 외부 커넥션이 먼저 이 계정의 락을 쥔다.
    assert try_lock_account(second_connection, account.id) is True

    run = CostIngestionRun(
        user_id=user.id, cloud_account_id=account.id, trigger_type="manual", status="running",
        period_start=dt.date(2026, 9, 1), period_end=dt.date(2026, 9, 18),
        requested_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(run)
    db_session.flush()

    with pytest.raises(AccountLockedError):
        replace_cost_rows(db_session, account, run, [], source="aws_cost_explorer")
