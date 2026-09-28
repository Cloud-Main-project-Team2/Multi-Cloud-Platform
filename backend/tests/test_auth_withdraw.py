"""DELETE /auth/me — 회원 탈퇴(2026-09-28).

soft 탈퇴(행 유지 · status=withdrawn)이되, 이메일을 풀어 같은 주소로 재가입할 수 있게 한다(사용자 결정).
등록한 키는 지우고 세션은 폐기한다. 실제 CSP는 호출하지 않는다.
"""

from __future__ import annotations

import datetime as dt

from app.models import (
    AuditEvent,
    CloudAccount,
    Credential,
    EmailVerification,
    ProvisioningJob,
    RefreshToken,
    ServiceCatalog,
    User,
)
from app.security.tokens import hash_token
from tests.account_helpers import add_credential

CONFIRM = {"X-Action-Confirmed": "true"}


def _account(db, user, provider="aws", ext="111122223333"):
    a = CloudAccount(user_id=user.id, provider=provider, external_account_id=ext)
    db.add(a)
    db.flush()
    return a


def test_withdraw_requires_confirmation(client, make_user, auth_header):
    user = make_user()
    resp = client.delete("/api/v1/auth/me", headers=auth_header(user))
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_withdraw_marks_user_and_frees_email(client, make_user, auth_header, db_session):
    user = make_user(email="leaver@example.com")
    acct = _account(db_session, user)
    add_credential(db_session, acct)
    add_credential(db_session, acct, name="second")
    db_session.add(RefreshToken(user_id=user.id, token_hash=hash_token("rt-1"),
                                expires_at=user.created_at.replace(year=2099)))
    db_session.flush()

    resp = client.delete("/api/v1/auth/me", headers={**auth_header(user), **CONFIRM})
    assert resp.status_code == 204

    db_session.expire_all()
    row = db_session.get(User, user.id)
    assert row.status == "withdrawn" and row.withdrawn_at is not None
    assert row.normalized_email == f"withdrawn-{user.id}@invalid" and row.email == row.normalized_email
    assert row.password_hash is None
    assert db_session.query(Credential).filter_by(cloud_account_id=acct.id).count() == 0
    assert db_session.get(CloudAccount, acct.id) is not None                      # 계정 행·이력은 남는다
    assert db_session.query(RefreshToken).filter_by(user_id=user.id, revoked_at=None).count() == 0
    audit = db_session.query(AuditEvent).filter_by(action="user.withdraw", actor_user_id=user.id).one()
    assert audit.result == "success"


def test_withdrawn_token_and_login_are_rejected(client, make_user, auth_header):
    user = make_user(email="gone@example.com", password="test-pass-1234")
    headers = auth_header(user)
    assert client.delete("/api/v1/auth/me", headers={**headers, **CONFIRM}).status_code == 204

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 401 and me.json()["error"]["code"] == "USER_WITHDRAWN"
    login = client.post("/api/v1/auth/login", json={"email": "gone@example.com", "password": "test-pass-1234"})
    assert login.status_code == 401


def test_same_email_can_sign_up_again(client, make_user, auth_header, db_session):
    user = make_user(email="again@example.com")
    assert client.delete("/api/v1/auth/me", headers={**auth_header(user), **CONFIRM}).status_code == 204

    # 탈퇴 전엔 이 단계가 409 EMAIL_ALREADY_EXISTS였다.
    assert client.post("/api/v1/auth/email-verifications", json={"email": "again@example.com"}).status_code == 201
    ev = db_session.query(EmailVerification).filter_by(normalized_email="again@example.com").one()
    ev.verified_at = dt.datetime.now(dt.timezone.utc)      # 메일로 받은 코드 확인을 대신한다
    db_session.flush()
    signup = client.post("/api/v1/auth/sign-up", json={
        "email": "again@example.com", "password": "correct-pass-1234", "name": "재가입", "affiliation_type": "individual",
    })
    assert signup.status_code == 201, signup.text
    assert signup.json()["data"]["id"] != str(user.id)


def test_withdraw_blocked_while_provisioning_runs(client, make_user, auth_header, db_session):
    user = make_user()
    acct = _account(db_session, user)
    cred = add_credential(db_session, acct)
    sc = ServiceCatalog(provider="aws", service_code="ec2", category="compute", display_name="EC2", provisionable=True)
    db_session.add(sc)
    db_session.flush()
    db_session.add(ProvisioningJob(user_id=user.id, credential_id=cred.id, service_catalog_id=sc.id,
                                   workspace_name="ws-1", idempotency_key="k-1", spec_json={}, status="running"))
    db_session.flush()

    resp = client.delete("/api/v1/auth/me", headers={**auth_header(user), **CONFIRM})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "JOB_ALREADY_RUNNING"
    db_session.expire_all()
    assert db_session.get(User, user.id).status == "active"
    assert db_session.query(Credential).filter_by(id=cred.id).count() == 1
