"""인증 강화 기능 검증: /me, refresh token, 이메일 검증(OTP), 비밀번호 재설정."""

import re

VERIFY_BODY = {
    "email": "flow-user@example.com",
    "password": "correct-pass-1234",
    "name": "홍길동",
    "affiliation_type": "individual",
}


def _extract_code(body: str) -> str:
    match = re.search(r"(\d{6})", body)
    assert match, f"코드가 메일 본문에 없음: {body}"
    return match.group(1)


# --- GET /auth/me -----------------------------------------------------------------------
def test_me_returns_current_user(client, make_user, auth_header):
    user = make_user(email="me@example.com")
    resp = client.get("/api/v1/auth/me", headers=auth_header(user))

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["email"] == "me@example.com"
    assert "password_hash" not in data


def test_me_without_token_is_unauthenticated(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


# --- login now returns refresh token + refresh/logout -----------------------------------
def test_login_returns_refresh_token(client, make_user):
    make_user(email="rt@example.com", password="correct-pass-1234")
    resp = client.post(
        "/api/v1/auth/login", json={"email": "rt@example.com", "password": "correct-pass-1234"}
    )
    body = resp.json()["data"]
    assert body["refresh_token"]
    assert body["refresh_expires_in"] > 0


def test_refresh_rotates_and_invalidates_old_token(client, make_user):
    make_user(email="rot@example.com", password="correct-pass-1234")
    login = client.post(
        "/api/v1/auth/login", json={"email": "rot@example.com", "password": "correct-pass-1234"}
    ).json()["data"]
    old_refresh = login["refresh_token"]

    first = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert first.status_code == 200
    new_refresh = first.json()["data"]["refresh_token"]
    assert new_refresh != old_refresh

    # 회전됐으므로 옛 토큰은 더 이상 유효하지 않다.
    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "INVALID_TOKEN"


def test_refresh_rejects_unknown_token(client):
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert resp.status_code == 401


def test_logout_revokes_refresh_token(client, make_user):
    make_user(email="lo@example.com", password="correct-pass-1234")
    login = client.post(
        "/api/v1/auth/login", json={"email": "lo@example.com", "password": "correct-pass-1234"}
    ).json()["data"]
    refresh = login["refresh_token"]

    out = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert out.status_code == 200

    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert reused.status_code == 401


# --- 이메일 검증(OTP) --------------------------------------------------------------------
def test_email_verification_full_flow_and_signup_gate(client, sent_emails):
    # 1) 코드 요청 → 메일 발송
    req = client.post("/api/v1/auth/email-verifications", json={"email": VERIFY_BODY["email"]})
    assert req.status_code == 201
    assert len(sent_emails) == 1
    code = _extract_code(sent_emails[0][2])

    # 2) 코드 확인
    verify = client.post(
        "/api/v1/auth/email-verifications/verify",
        json={"email": VERIFY_BODY["email"], "code": code},
    )
    assert verify.status_code == 200
    assert verify.json()["data"]["verified"] is True

    # 3) 검증했으니 가입 성공
    signup = client.post("/api/v1/auth/sign-up", json=VERIFY_BODY)
    assert signup.status_code == 201


def test_sign_up_without_verification_is_rejected(client):
    resp = client.post("/api/v1/auth/sign-up", json=VERIFY_BODY)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_email_verification_rejects_existing_email(client, make_user):
    make_user(email="taken@example.com")
    resp = client.post("/api/v1/auth/email-verifications", json={"email": "taken@example.com"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


def test_email_verification_wrong_code_increments_attempts(client, sent_emails):
    client.post("/api/v1/auth/email-verifications", json={"email": "wrong@example.com"})
    resp = client.post(
        "/api/v1/auth/email-verifications/verify",
        json={"email": "wrong@example.com", "code": "000000"},
    )
    # 실제 코드와 다르면(우연히 일치가 아니면) 400.
    assert resp.status_code in (400,)
    assert resp.json()["error"]["code"] in ("INVALID_VERIFICATION_CODE",)


def test_email_verification_resend_rate_limited(client, sent_emails):
    client.post("/api/v1/auth/email-verifications", json={"email": "rl@example.com"})
    again = client.post("/api/v1/auth/email-verifications", json={"email": "rl@example.com"})
    assert again.status_code == 429
    assert again.json()["error"]["code"] == "TOO_MANY_REQUESTS"


# --- 비밀번호 재설정 ---------------------------------------------------------------------
def test_password_reset_full_flow(client, make_user, sent_emails):
    make_user(email="pr@example.com", password="old-pass-1234")
    req = client.post("/api/v1/auth/password-reset", json={"email": "pr@example.com"})
    assert req.status_code == 202
    assert len(sent_emails) == 1

    token_match = re.search(r"token=([\w\-]+)", sent_emails[0][2])
    assert token_match
    token = token_match.group(1)

    confirm = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "new-pass-5678"},
    )
    assert confirm.status_code == 200

    # 새 비밀번호로 로그인 가능, 옛 비밀번호는 실패.
    ok = client.post("/api/v1/auth/login", json={"email": "pr@example.com", "password": "new-pass-5678"})
    assert ok.status_code == 200
    bad = client.post("/api/v1/auth/login", json={"email": "pr@example.com", "password": "old-pass-1234"})
    assert bad.status_code == 401


def test_password_reset_unknown_email_still_returns_202(client, sent_emails):
    resp = client.post("/api/v1/auth/password-reset", json={"email": "nobody@example.com"})
    assert resp.status_code == 202
    assert sent_emails == []  # 계정이 없으면 실제 발송은 없다.


def test_password_reset_confirm_rejects_invalid_token(client):
    resp = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "bogus", "new_password": "new-pass-5678"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_RESET_TOKEN"
