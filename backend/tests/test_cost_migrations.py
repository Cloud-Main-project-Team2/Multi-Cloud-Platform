"""PR 3 — 비용·팀·예산·검토 스키마 마이그레이션 검증(R1~R4).

upgrade -> downgrade -> upgrade 왕복과, docs/DB_ERD_v1.2.md Part B가 정본으로 둔 UNIQUE 4개·
대표 CHECK가 실제로 막히는지 확인한다. test_migration_backfill.py와 같은 패턴 — 전용 DB에
Base.metadata.create_all()이 아니라 실제 alembic upgrade/downgrade 경로를 실행한다.
"""

import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings

BASE_REVISION = "dde461ba65fd"  # PR 1의 R0(status_changed_at) — 이 스키마 세트의 출발점
R1 = "fa0ba565197b"
HEAD_REVISION = "24378fc90e8d"  # R4
MIGRATION_TEST_DB_NAME = "mcp_db_cost_migration_test"

NEW_TABLES = (
    "teams",
    "team_budgets",
    "team_budget_notifications",
    "cloud_account_costs",
    "cost_ingestion_runs",
    "cost_review_items",
)


def _app_database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql+psycopg2://mcp_user:change_me@db:5432/mcp_db")


def _admin_database_url() -> str:
    return _app_database_url().rsplit("/", 1)[0] + "/postgres"


def _migration_test_database_url() -> str:
    prefix, _, _ = _app_database_url().rpartition("/")
    return f"{prefix}/{MIGRATION_TEST_DB_NAME}"


@pytest.fixture()
def migration_db_url():
    admin_engine = create_engine(_admin_database_url(), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {MIGRATION_TEST_DB_NAME} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {MIGRATION_TEST_DB_NAME}"))
    admin_engine.dispose()

    yield _migration_test_database_url()

    admin_engine = create_engine(_admin_database_url(), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {MIGRATION_TEST_DB_NAME} WITH (FORCE)"))
    admin_engine.dispose()


@pytest.fixture()
def alembic_config(migration_db_url):
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = migration_db_url
    get_settings.cache_clear()

    cfg = Config("alembic.ini")
    yield cfg

    if original_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = original_url
    get_settings.cache_clear()


def _insert_base_dataset(engine) -> dict:
    """R1~R4가 참조하는 users/cloud_accounts 최소 행을 심는다."""
    with engine.begin() as conn:
        user_id = conn.execute(
            text(
                "INSERT INTO users (email, normalized_email, password_hash, name, "
                "affiliation_type, status) VALUES "
                "('cost@example.com','cost@example.com','x-hash','Cost Tester','individual','active') "
                "RETURNING id"
            )
        ).scalar_one()
        cloud_account_id = conn.execute(
            text(
                "INSERT INTO cloud_accounts (user_id, provider, external_account_id, account_label) "
                "VALUES (:user_id, 'aws', '111111111111', 'Cost AWS') RETURNING id"
            ),
            {"user_id": user_id},
        ).scalar_one()
    return {"user_id": user_id, "cloud_account_id": cloud_account_id}


def _tables(migration_db_url) -> set[str]:
    with create_engine(migration_db_url, future=True).connect() as conn:
        return set(sa.inspect(conn).get_table_names())


def test_cost_schema_upgrade_downgrade_roundtrip(alembic_config, migration_db_url):
    command.upgrade(alembic_config, BASE_REVISION)
    command.upgrade(alembic_config, HEAD_REVISION)

    tables = _tables(migration_db_url)
    for t in NEW_TABLES:
        assert t in tables
    with create_engine(migration_db_url, future=True).connect() as conn:
        cloud_account_columns = {c["name"] for c in sa.inspect(conn).get_columns("cloud_accounts")}
    assert "team_id" in cloud_account_columns

    # R4 -> R1 -> R0까지 하나씩 되돌린다
    command.downgrade(alembic_config, BASE_REVISION)
    tables = _tables(migration_db_url)
    for t in NEW_TABLES:
        assert t not in tables
    with create_engine(migration_db_url, future=True).connect() as conn:
        cloud_account_columns = {c["name"] for c in sa.inspect(conn).get_columns("cloud_accounts")}
    assert "team_id" not in cloud_account_columns

    # 다시 head로 — 왕복 후에도 깨끗하게 재적용된다
    command.upgrade(alembic_config, HEAD_REVISION)
    tables = _tables(migration_db_url)
    for t in NEW_TABLES:
        assert t in tables


def test_r1_alone_is_a_valid_stopping_point(alembic_config, migration_db_url):
    """R1만 적용해도(R2~R4 없이) teams + cloud_accounts.team_id만 생기고 나머지는 없다 —
    리비전 4개가 실제로 분리돼 있는지 확인한다."""
    command.upgrade(alembic_config, R1)

    tables = _tables(migration_db_url)
    assert "teams" in tables
    for t in NEW_TABLES:
        if t != "teams":
            assert t not in tables


def test_teams_unique_user_name(alembic_config, migration_db_url):
    command.upgrade(alembic_config, HEAD_REVISION)
    engine = create_engine(migration_db_url, future=True)
    ids = _insert_base_dataset(engine)

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO teams (user_id, name) VALUES (:uid, '운영팀')"), {"uid": ids["user_id"]})

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO teams (user_id, name) VALUES (:uid, '운영팀')"), {"uid": ids["user_id"]}
            )


def test_team_budgets_limit_must_be_positive(alembic_config, migration_db_url):
    command.upgrade(alembic_config, HEAD_REVISION)
    engine = create_engine(migration_db_url, future=True)
    ids = _insert_base_dataset(engine)

    with engine.begin() as conn:
        team_id = conn.execute(
            text("INSERT INTO teams (user_id, name) VALUES (:uid, '운영팀') RETURNING id"),
            {"uid": ids["user_id"]},
        ).scalar_one()

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO team_budgets (team_id, period_type, start_date, limit_amount) "
                    "VALUES (:team_id, 'monthly', '2026-09-01', 0)"
                ),
                {"team_id": team_id},
            )


def test_team_budgets_custom_requires_end_date(alembic_config, migration_db_url):
    command.upgrade(alembic_config, HEAD_REVISION)
    engine = create_engine(migration_db_url, future=True)
    ids = _insert_base_dataset(engine)

    with engine.begin() as conn:
        team_id = conn.execute(
            text("INSERT INTO teams (user_id, name) VALUES (:uid, '운영팀') RETURNING id"),
            {"uid": ids["user_id"]},
        ).scalar_one()

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO team_budgets (team_id, period_type, start_date, limit_amount) "
                    "VALUES (:team_id, 'custom', '2026-09-01', 100)"
                ),
                {"team_id": team_id},
            )


def test_team_budget_notifications_unique_key(alembic_config, migration_db_url):
    command.upgrade(alembic_config, HEAD_REVISION)
    engine = create_engine(migration_db_url, future=True)
    ids = _insert_base_dataset(engine)

    with engine.begin() as conn:
        team_id = conn.execute(
            text("INSERT INTO teams (user_id, name) VALUES (:uid, '운영팀') RETURNING id"),
            {"uid": ids["user_id"]},
        ).scalar_one()
        budget_id = conn.execute(
            text(
                "INSERT INTO team_budgets (team_id, period_type, start_date, limit_amount) "
                "VALUES (:team_id, 'monthly', '2026-09-01', 300) RETURNING id"
            ),
            {"team_id": team_id},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO team_budget_notifications (team_budget_id, period_start, threshold) "
                "VALUES (:budget_id, '2026-09-01', 80)"
            ),
            {"budget_id": budget_id},
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO team_budget_notifications (team_budget_id, period_start, threshold) "
                    "VALUES (:budget_id, '2026-09-01', 80)"
                ),
                {"budget_id": budget_id},
            )


def test_cloud_account_costs_allows_negative_amount_and_blocks_duplicate_source_key(
    alembic_config, migration_db_url
):
    command.upgrade(alembic_config, HEAD_REVISION)
    engine = create_engine(migration_db_url, future=True)
    ids = _insert_base_dataset(engine)

    with engine.begin() as conn:
        # 크레딧(음수) — amount에 CHECK가 없으므로 성공해야 한다(cloud_resource_costs와 다른 점)
        conn.execute(
            text(
                "INSERT INTO cloud_account_costs "
                "(cloud_account_id, provider, charge_category, amount, currency, "
                "period_start, period_end, as_of, source, source_record_key) VALUES "
                "(:cid, 'aws', 'credit', -30.00, 'USD', '2026-09-01', '2026-09-02', now(), "
                "'aws_cost_explorer', 'aws:111111111111:2026-09-01::Credit')"
            ),
            {"cid": ids["cloud_account_id"]},
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO cloud_account_costs "
                    "(cloud_account_id, provider, charge_category, amount, currency, "
                    "period_start, period_end, as_of, source, source_record_key) VALUES "
                    "(:cid, 'aws', 'credit', -10.00, 'USD', '2026-09-01', '2026-09-02', now(), "
                    "'aws_cost_explorer', 'aws:111111111111:2026-09-01::Credit')"
                ),
                {"cid": ids["cloud_account_id"]},
            )


def test_cost_ingestion_runs_period_end_after_start(alembic_config, migration_db_url):
    command.upgrade(alembic_config, HEAD_REVISION)
    engine = create_engine(migration_db_url, future=True)
    ids = _insert_base_dataset(engine)

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO cost_ingestion_runs "
                    "(user_id, cloud_account_id, trigger_type, period_start, period_end, requested_at) "
                    "VALUES (:uid, :cid, 'manual', '2026-09-05', '2026-09-01', now())"
                ),
                {"uid": ids["user_id"], "cid": ids["cloud_account_id"]},
            )


def test_cost_review_items_unique_source_and_resolved_consistency(alembic_config, migration_db_url):
    command.upgrade(alembic_config, HEAD_REVISION)
    engine = create_engine(migration_db_url, future=True)
    ids = _insert_base_dataset(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO cost_review_items (user_id, source_type, source_key) "
                "VALUES (:uid, 'cost_anomaly', '31:AmazonRDS:2026-09-13')"
            ),
            {"uid": ids["user_id"]},
        )

    # 같은 (user_id, source_type, source_key) 재삽입 — 최초 1회 알림을 보장하는 UNIQUE
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO cost_review_items (user_id, source_type, source_key) "
                    "VALUES (:uid, 'cost_anomaly', '31:AmazonRDS:2026-09-13')"
                ),
                {"uid": ids["user_id"]},
            )

    # status='resolved'인데 resolution이 없으면 거부된다
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO cost_review_items (user_id, source_type, source_key, status) "
                    "VALUES (:uid, 'cost_anomaly', '31:AmazonRDS:2026-09-20', 'resolved')"
                ),
                {"uid": ids["user_id"]},
            )


# --- R2 보강(2026-09-20, b7c2d9e4f1a3): 반복 예산 (team_id, start_date) 부분 UNIQUE -------------

R2_RECURRING_UNIQUE = "b7c2d9e4f1a3"


def test_team_budgets_recurring_same_start_date_blocked_but_custom_allowed(alembic_config, migration_db_url):
    command.upgrade(alembic_config, R2_RECURRING_UNIQUE)
    engine = create_engine(migration_db_url, future=True)
    ids = _insert_base_dataset(engine)

    with engine.begin() as conn:
        team_id = conn.execute(
            text("INSERT INTO teams (user_id, name) VALUES (:uid, '운영팀') RETURNING id"), {"uid": ids["user_id"]}
        ).scalar_one()
        conn.execute(
            text("INSERT INTO team_budgets (team_id, period_type, start_date, limit_amount) VALUES (:t, 'monthly', '2026-09-01', 300)"),
            {"t": team_id},
        )
        # custom은 대상이 아니다 — 같은 시작일이어도 들어간다(겹침은 애플리케이션이 검사)
        conn.execute(
            text("INSERT INTO team_budgets (team_id, period_type, start_date, end_date, limit_amount) VALUES (:t, 'custom', '2026-09-01', '2026-09-15', 50)"),
            {"t": team_id},
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO team_budgets (team_id, period_type, start_date, limit_amount) VALUES (:t, 'quarterly', '2026-09-01', 500)"),
                {"t": team_id},
            )

    # downgrade하면 인덱스가 사라진다
    command.downgrade(alembic_config, HEAD_REVISION)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO team_budgets (team_id, period_type, start_date, limit_amount) VALUES (:t, 'quarterly', '2026-09-01', 500)"),
            {"t": team_id},
        )
    engine.dispose()
