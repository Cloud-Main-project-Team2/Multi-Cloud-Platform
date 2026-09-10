"""POST /api/v1/auth/login 최소 구현 검증."""

import jwt


def test_login_success_returns_access_token(client, make_user):
    make_user(email="login@example.com", password="correct-pass-1234")

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "login@example.com", "password": "correct-pass-1234"},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "login@example.com"
    # normalized_email/password_hash는 응답에 없어야 한다(§3.1).
    assert "normalized_email" not in body["user"]
    assert "password_hash" not in body["user"]

    decoded = jwt.decode(body["access_token"], "test-only-jwt-secret", algorithms=["HS256"])
    assert decoded["sub"] == body["user"]["id"]


def test_login_wrong_password_returns_invalid_credentials(client, make_user):
    make_user(email="login2@example.com", password="correct-pass-1234")

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "login2@example.com", "password": "wrong-password"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_unknown_email_returns_same_error_as_wrong_password(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "no-such-user@example.com", "password": "whatever-1234"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_withdrawn_user_is_rejected(client, make_user):
    make_user(email="withdrawn@example.com", password="correct-pass-1234", status="withdrawn")

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "withdrawn@example.com", "password": "correct-pass-1234"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "USER_WITHDRAWN"


def test_protected_route_without_token_requires_authentication(client):
    resp = client.get("/api/v1/cloud-accounts")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_protected_route_with_garbage_token_is_invalid(client):
    resp = client.get("/api/v1/cloud-accounts", headers={"Authorization": "Bearer not-a-real-token"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"
