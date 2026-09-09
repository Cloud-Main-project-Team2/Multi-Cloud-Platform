from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import app.main as main_module


def test_health_ok():
    client = TestClient(main_module.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_ok():
    client = TestClient(main_module.app)
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_reports_503_when_db_unavailable(monkeypatch):
    broken_engine = create_engine(
        "postgresql+psycopg2://baduser:badpass@127.0.0.1:59999/baddb",
        pool_pre_ping=False,
    )
    monkeypatch.setattr(main_module, "engine", broken_engine)

    client = TestClient(main_module.app)
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}
