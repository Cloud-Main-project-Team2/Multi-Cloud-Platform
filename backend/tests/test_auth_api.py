"""POST /api/v1/auth/login, POST /api/v1/auth/sign-up 검증."""

import jwt

SIGNUP_BODY = {
    "email": "new-user@example.com",
    "password": "correct-pass-1234",
    "name": "홍길동",
    "affiliation_type": "individual",
}


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


# --- POST /auth/sign-up -----------------------------------------------------------------


def test_sign_up_creates_user_and_allows_login(client):
    resp = client.post("/api/v1/auth/sign-up", json=SIGNUP_BODY)

    assert resp.status_code == 201
    user = resp.json()["data"]
    assert user["email"] == SIGNUP_BODY["email"]
    assert user["status"] == "active"
    assert "normalized_email" not in user
    assert "password_hash" not in user

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": SIGNUP_BODY["email"], "password": SIGNUP_BODY["password"]},
    )
    assert login_resp.status_code == 200


def test_sign_up_normalizes_email_case_for_duplicate_check(client):
    client.post("/api/v1/auth/sign-up", json=SIGNUP_BODY)

    resp = client.post(
        "/api/v1/auth/sign-up",
        json={**SIGNUP_BODY, "email": SIGNUP_BODY["email"].upper()},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


def test_sign_up_rejects_invalid_email_format(client):
    resp = client.post("/api/v1/auth/sign-up", json={**SIGNUP_BODY, "email": "not-an-email"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_sign_up_rejects_weak_password(client):
    resp = client.post("/api/v1/auth/sign-up", json={**SIGNUP_BODY, "password": "onlyletters"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_sign_up_requires_affiliation_name_when_company(client):
    resp = client.post(
        "/api/v1/auth/sign-up",
        json={**SIGNUP_BODY, "email": "company-user@example.com", "affiliation_type": "company"},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_sign_up_accepts_company_with_affiliation_name(client):
    resp = client.post(
        "/api/v1/auth/sign-up",
        json={
            **SIGNUP_BODY,
            "email": "company-user2@example.com",
            "affiliation_type": "company",
            "affiliation_name": "Example Corp",
        },
    )

    assert resp.status_code == 201
    assert resp.json()["data"]["affiliation_name"] == "Example Corp"
