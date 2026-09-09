import os

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — register models on Base.metadata
from app.db import Base

DEFAULT_DATABASE_URL = "postgresql+psycopg2://mcp_user:change_me@db:5432/mcp_db"


def _app_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _admin_database_url() -> str:
    return _app_database_url().rsplit("/", 1)[0] + "/postgres"


def _test_database_url() -> str:
    prefix, _, _ = _app_database_url().rpartition("/")
    return f"{prefix}/mcp_db_test"


@pytest.fixture(scope="session")
def test_db_url() -> str:
    admin_engine = create_engine(_admin_database_url(), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": "mcp_db_test"},
        ).scalar()
        if not exists:
            conn.execute(sa.text("CREATE DATABASE mcp_db_test"))
    admin_engine.dispose()
    return _test_database_url()


@pytest.fixture(scope="session")
def engine(test_db_url):
    eng = create_engine(test_db_url, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(bind=connection, future=True)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
