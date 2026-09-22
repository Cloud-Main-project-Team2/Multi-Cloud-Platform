"""알림 조회/읽음 처리/삭제 API 테스트
(GET /notifications, POST /notifications/read-all, DELETE /notifications[/{id}]).

알림 row 생성은 프로비저닝 라우터가 담당하므로, 여기서는 조회 스코프·미확인 개수·읽음
처리·삭제만 검증한다.
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


def test_delete_one_removes_only_that_row(client, db_session, make_user, auth_header):
    user = make_user()
    n1 = _make_notif(db_session, user)
    n2 = _make_notif(db_session, user)
    db_session.commit()

    resp = client.delete(f"/api/v1/notifications/{n1.id}", headers=auth_header(user))
    assert resp.status_code == 204

    items = client.get("/api/v1/notifications", headers=auth_header(user)).json()["data"]["items"]
    assert [i["id"] for i in items] == [str(n2.id)]


def test_delete_one_rejects_other_users_notification(client, db_session, make_user, auth_header):
    owner = make_user()
    other = make_user(email="other2@example.com")
    n = _make_notif(db_session, owner)
    db_session.commit()

    resp = client.delete(f"/api/v1/notifications/{n.id}", headers=auth_header(other))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOTIFICATION_NOT_FOUND"

    # 소유자 목록에는 그대로 남아 있어야 한다 — 다른 사용자가 지울 수 없음.
    items = client.get("/api/v1/notifications", headers=auth_header(owner)).json()["data"]["items"]
    assert len(items) == 1


def test_delete_one_missing_id_is_404(client, make_user, auth_header):
    user = make_user()
    resp = client.delete("/api/v1/notifications/999999", headers=auth_header(user))
    assert resp.status_code == 404


def test_delete_all_clears_only_own_notifications(client, db_session, make_user, auth_header):
    user = make_user()
    other = make_user(email="other3@example.com")
    _make_notif(db_session, user)
    _make_notif(db_session, user)
    _make_notif(db_session, other)
    db_session.commit()

    resp = client.delete("/api/v1/notifications", headers=auth_header(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] == 2

    assert client.get("/api/v1/notifications", headers=auth_header(user)).json()["data"]["items"] == []
    # 다른 사용자 알림은 그대로.
    assert len(client.get("/api/v1/notifications", headers=auth_header(other)).json()["data"]["items"]) == 1
