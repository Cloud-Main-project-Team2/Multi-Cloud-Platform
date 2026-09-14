import base64
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
import app.models  # noqa: F401 — register models on Base.metadata
import app.routers.auth as auth_module
from app.config import get_settings
from app.db import Base, get_db
from app.models import EmailVerification, User
from app.security.jwt_tokens import create_access_token

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


@pytest.fixture(autouse=True)
def _default_app_secrets(monkeypatch):
    """credentials/auth 라우터가 쓰는 설정값의 기본값. 개별 테스트가 필요 시 재정의한다."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    main_module.app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(main_module.app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        main_module.app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def make_user(db_session):
    def _make(email: str = "user@example.com", password: str = "test-pass-1234", **overrides) -> User:
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        defaults = dict(
            email=email,
            normalized_email=email.strip().casefold(),
            password_hash=password_hash,
            name="Test User",
            affiliation_type="individual",
            status="active",
        )
        defaults.update(overrides)
        user = User(**defaults)
        db_session.add(user)
        db_session.flush()
        return user

    return _make


@pytest.fixture()
def auth_header():
    def _header(user: User) -> dict[str, str]:
        token, _ = create_access_token(user.id)
        return {"Authorization": f"Bearer {token}"}

    return _header


@pytest.fixture()
def verify_email(db_session):
    """회원가입 게이트를 통과시키기 위해 이메일을 '검증 완료' 상태로 심어 둔다."""

    def _verify(email: str) -> EmailVerification:
        row = EmailVerification(
            normalized_email=email.strip().casefold(),
            code_hash="seeded",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            verified_at=datetime.now(timezone.utc),
        )
        db_session.add(row)
        db_session.flush()
        return row

    return _verify


@pytest.fixture()
def sent_emails(monkeypatch):
    """auth 라우터가 발송하는 메일을 가로채 (to, subject, body) 튜플 리스트로 모은다."""
    outbox: list[tuple[str, str, str]] = []

    def _capture(to: str, subject: str, body: str) -> None:
        outbox.append((to, subject, body))

    monkeypatch.setattr(auth_module, "send_email", _capture)
    return outbox
