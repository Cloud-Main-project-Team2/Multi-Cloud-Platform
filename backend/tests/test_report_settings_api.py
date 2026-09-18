"""GET/PUT/DELETE /reports/settings 검증(2026-09-19) — 보고서 "정기 발송" 설정 저장.

지금까지 이 설정은 브라우저 localStorage에만 있었다 — 이 테스트는 실제로 서버에 저장되고,
사용자별로 격리되는지 확인한다.
"""

from __future__ import annotations

from app.models import ReportDeliverySetting


def test_get_settings_defaults_to_web_weekly_when_nothing_saved(client, make_user, auth_header):
    user = make_user()
    resp = client.get("/api/v1/reports/settings", headers=auth_header(user))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["delivery_method"] == "WEB"
    assert data["email"] is None
    assert data["period_type"] == "WEEKLY"
    assert data["last_sent_at"] is None
    assert 0 <= data["send_hour_kst"] <= 23


def test_put_settings_creates_row(client, make_user, auth_header, db_session):
    user = make_user()
    resp = client.put(
        "/api/v1/reports/settings",
        json={"delivery_method": "EMAIL", "email": "me@example.com", "period_type": "DAILY"},
        headers=auth_header(user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["delivery_method"] == "EMAIL"
    assert data["email"] == "me@example.com"
    assert data["period_type"] == "DAILY"

    row = db_session.query(ReportDeliverySetting).filter_by(user_id=user.id).one()
    assert row.delivery_method == "EMAIL"
    assert row.email == "me@example.com"


def test_put_settings_email_requires_address(client, make_user, auth_header):
    user = make_user()
    resp = client.put(
        "/api/v1/reports/settings",
        json={"delivery_method": "EMAIL", "email": "", "period_type": "WEEKLY"},
        headers=auth_header(user),
    )
    assert resp.status_code == 422


def test_put_settings_updates_existing_row_instead_of_duplicating(client, make_user, auth_header, db_session):
    user = make_user()
    client.put(
        "/api/v1/reports/settings",
        json={"delivery_method": "EMAIL", "email": "old@example.com", "period_type": "WEEKLY"},
        headers=auth_header(user),
    )
    resp = client.put(
        "/api/v1/reports/settings",
        json={"delivery_method": "EMAIL", "email": "new@example.com", "period_type": "MONTHLY"},
        headers=auth_header(user),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "new@example.com"
    assert db_session.query(ReportDeliverySetting).filter_by(user_id=user.id).count() == 1


def test_delete_settings_removes_row(client, make_user, auth_header, db_session):
    user = make_user()
    client.put(
        "/api/v1/reports/settings",
        json={"delivery_method": "EMAIL", "email": "me@example.com", "period_type": "WEEKLY"},
        headers=auth_header(user),
    )
    resp = client.delete("/api/v1/reports/settings", headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["delivery_method"] == "WEB"
    assert db_session.query(ReportDeliverySetting).filter_by(user_id=user.id).one_or_none() is None


def test_settings_are_isolated_per_user(client, make_user, auth_header):
    owner = make_user(email="owner-rs@example.com")
    other = make_user(email="other-rs@example.com")
    client.put(
        "/api/v1/reports/settings",
        json={"delivery_method": "EMAIL", "email": "owner@example.com", "period_type": "WEEKLY"},
        headers=auth_header(owner),
    )
    resp = client.get("/api/v1/reports/settings", headers=auth_header(other))
    assert resp.json()["data"]["delivery_method"] == "WEB"


def test_requires_auth(client):
    resp = client.get("/api/v1/reports/settings")
    assert resp.status_code == 401
