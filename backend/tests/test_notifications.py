"""알림 조회/읽음 처리 API 테스트 (GET /notifications, POST /notifications/read-all).

알림 row 생성은 프로비저닝 라우터가 담당하므로, 여기서는 조회 스코프·미확인 개수·읽음
처리만 검증한다.
"""

from __future__ import annotations

from app.models import Notification


def _make_notif(db, user, **over):
    n = Notification(
        user_id=user.id,
        type=over.get("type", "provisioning_succeeded"),
        reference_type=over.get("reference_type", "provisioning_job"),
        reference_id=over.get("reference_id", 1),
        message_key=over.get("message_key", "notif.provisioning.succeeded"),
        message_params=over.get("message_params", {"resource": "mcp-vm"}),
        is_read=over.get("is_read", False),
    )
    db.add(n)
    db.flush()
    return n


def test_list_requires_auth(client):
    assert client.get("/api/v1/notifications").status_code == 401


def test_list_returns_only_owned_with_unread_count(client, db_session, make_user, auth_header):
    user = make_user()
    other = make_user(email="other@example.com")
    _make_notif(db_session, user)
    _make_notif(db_session, user, is_read=True)
    _make_notif(db_session, other)  # 다른 사용자 것은 보이면 안 됨

    resp = client.get("/api/v1/notifications", headers=auth_header(user))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["items"]) == 2
    assert data["unread_count"] == 1
    assert data["items"][0]["type"] == "provisioning_succeeded"
    assert data["items"][0]["message_params"]["resource"] == "mcp-vm"


def test_read_all_zeroes_unread(client, db_session, make_user, auth_header):
    user = make_user()
    _make_notif(db_session, user)
    _make_notif(db_session, user)

    resp = client.post("/api/v1/notifications/read-all", headers=auth_header(user))
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["unread_count"] == 0
    assert body["updated"] == 2

    again = client.get("/api/v1/notifications", headers=auth_header(user))
    assert again.json()["data"]["unread_count"] == 0
