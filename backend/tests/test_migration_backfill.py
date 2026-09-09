"""Exercises the phase-2 Alembic migration (2964dfe0a706) against a database that
already holds pre-existing (1차) data, the way it would in a real deployment.

Unlike the other tests, this does not use Base.metadata.create_all() — it runs the
actual Alembic upgrade/downgrade path so the staged nullable -> backfill ->
validate -> NOT NULL logic in the migration itself is verified, not just the
final ORM model shape.
"""

import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.seed import SERVICE_CATALOG_SEED, seed_service_catalog

BASE_REVISION = "7bf7892874c3"
HEAD_REVISION = "2964dfe0a706"
MIGRATION_TEST_DB_NAME = "mcp_db_migration_test"


def _app_database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://mcp_user:change_me@db:5432/mcp_db"
    )


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


def _insert_legacy_dataset(engine) -> dict:
    """Seeds service_catalog and inserts one representative row per pre-existing
    (1차) table, simulating a real deployment's data at the moment phase-2 runs."""
    with engine.begin() as conn:
        session = Session(bind=conn)
        seed_service_catalog(session, rows=SERVICE_CATALOG_SEED)
        session.flush()

        user_id = conn.execute(
            text(
                "INSERT INTO users (email, normalized_email, password_hash, name, "
                "affiliation_type, status) VALUES "
                "('legacy@example.com','legacy@example.com','x-hash','Legacy User','company','active') "
                "RETURNING id"
            )
        ).scalar_one()
        cloud_account_id = conn.execute(
            text(
                "INSERT INTO cloud_accounts (user_id, provider, external_account_id, account_label) "
                "VALUES (:user_id, 'aws', '111111111111', 'Legacy AWS') RETURNING id"
            ),
            {"user_id": user_id},
        ).scalar_one()
        credential_id = conn.execute(
            text(
                "INSERT INTO credentials (cloud_account_id, name, encrypted_payload, "
                "encryption_nonce, encryption_key_version) VALUES "
                "(:cloud_account_id, 'legacy-cred', '\\x00', '\\x00', 'v1') RETURNING id"
            ),
            {"cloud_account_id": cloud_account_id},
        ).scalar_one()
        ec2_service_catalog_id = conn.execute(
            text("SELECT id FROM service_catalog WHERE provider = 'aws' AND service_code = 'ec2'")
        ).scalar_one()
        resource_id = conn.execute(
            text(
                "INSERT INTO resources (cloud_account_id, service_catalog_id, "
                "first_collected_by_credential_id, last_collected_by_credential_id, "
                "provider_resource_key, external_resource_id, original_resource_type, "
                "name, region, status, first_seen_at, last_seen_at) VALUES "
                "(:cloud_account_id, :service_catalog_id, :credential_id, :credential_id, "
                "'arn:aws:ec2:ap-northeast-2:111111111111:instance/i-legacy01', 'i-legacy01', "
                "'AWS::EC2::Instance', 'legacy-web-01', 'ap-northeast-2', 'running', now(), now()) "
                "RETURNING id"
            ),
            {
                "cloud_account_id": cloud_account_id,
                "service_catalog_id": ec2_service_catalog_id,
                "credential_id": credential_id,
            },
        ).scalar_one()
        job_id = conn.execute(
            text(
                "INSERT INTO provisioning_jobs (user_id, credential_id, service_catalog_id, "
                "workspace_name, idempotency_key, spec_json, status, started_at, finished_at) "
                "VALUES (:user_id, :credential_id, :service_catalog_id, 'legacy-workspace-01', "
                "'legacy-idem-01', '{\"instance_type\": \"t3.large\"}'::jsonb, 'success', now(), now()) "
                "RETURNING id"
            ),
            {
                "user_id": user_id,
                "credential_id": credential_id,
                "service_catalog_id": ec2_service_catalog_id,
            },
        ).scalar_one()

    return {
        "user_id": user_id,
        "cloud_account_id": cloud_account_id,
        "credential_id": credential_id,
        "ec2_service_catalog_id": ec2_service_catalog_id,
        "resource_id": resource_id,
        "job_id": job_id,
    }


def test_phase2_migration_preserves_and_backfills_existing_data(alembic_config, migration_db_url):
    # 1) 1차 스키마만 적용
    command.upgrade(alembic_config, BASE_REVISION)

    setup_engine = create_engine(migration_db_url, future=True)
    ids = _insert_legacy_dataset(setup_engine)
    setup_engine.dispose()

    # 2) phase-2 migration 적용 — 기존 데이터가 있는 상태에서 성공해야 한다
    command.upgrade(alembic_config, HEAD_REVISION)

    verify_engine = create_engine(migration_db_url, future=True)
    with verify_engine.connect() as conn:
        # 기존 데이터가 보존됐는지
        assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM cloud_accounts")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM credentials")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM resources")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM provisioning_jobs")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM service_catalog")).scalar_one() == 12

        # resource_types 최소 시드
        assert conn.execute(text("SELECT count(*) FROM resource_types")).scalar_one() == 12

        # 기존 provisioning_jobs가 합성 provisioning_requests 부모로 backfill됨
        row = conn.execute(
            text(
                "SELECT pj.provisioning_request_id, pr.id AS request_id, pr.request_key, "
                "pr.resource_type_id, pr.user_id "
                "FROM provisioning_jobs pj "
                "JOIN provisioning_requests pr ON pr.id = pj.provisioning_request_id "
                "WHERE pj.id = :job_id"
            ),
            {"job_id": ids["job_id"]},
        ).one()
        assert row.provisioning_request_id == row.request_id
        assert row.request_key == f"legacy-job-{ids['job_id']}"
        assert row.user_id == ids["user_id"]
        assert row.resource_type_id is not None

        # provisioning_request_id는 backfill 후 NOT NULL로 강화됨
        is_nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'provisioning_jobs' AND column_name = 'provisioning_request_id'"
            )
        ).scalar_one()
        assert is_nullable == "NO"

        # 기존 resources 행도 확실하게 매핑되는 경우 resource_type_id가 backfill됨
        resource_type_id = conn.execute(
            text("SELECT resource_type_id FROM resources WHERE id = :resource_id"),
            {"resource_id": ids["resource_id"]},
        ).scalar_one()
        assert resource_type_id is not None

        # 새로 추가된 resources.resource_type_id 자체는 여전히 nullable(수집 코드가 항상
        # 채우게 되기 전까지)
        resources_nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'resources' AND column_name = 'resource_type_id'"
            )
        ).scalar_one()
        assert resources_nullable == "YES"

    verify_engine.dispose()

    # 3) downgrade -> upgrade round trip: 1차 데이터가 살아남고 재적용도 성공해야 한다
    command.downgrade(alembic_config, BASE_REVISION)

    with create_engine(migration_db_url, future=True).connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        assert "provisioning_requests" not in tables
        assert "resource_types" not in tables
        assert "cloud_resource_costs" not in tables
        assert "audit_events" not in tables
        assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM provisioning_jobs")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM resources")).scalar_one() == 1

    command.upgrade(alembic_config, HEAD_REVISION)

    with create_engine(migration_db_url, future=True).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM resource_types")).scalar_one() == 12
        assert conn.execute(text("SELECT count(*) FROM provisioning_requests")).scalar_one() == 1
        assert (
            conn.execute(
                text("SELECT provisioning_request_id FROM provisioning_jobs WHERE id = :job_id"),
                {"job_id": ids["job_id"]},
            ).scalar_one()
            is not None
        )
