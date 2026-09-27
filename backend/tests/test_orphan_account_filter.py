"""자격 증명을 모두 지운 클라우드 계정은 화면(조회)에서 빠지고, 다시 등록하면 이력과 함께 돌아온다.

마이페이지 삭제(`DELETE /credentials/{id}`)는 credential 행만 지우고 계정 행은 남긴다 —
계정 삭제 API는 보존 정책 미확정으로 501 보류다(`app/account_scope.py` 머리 주석).
"""

import datetime as dt

from app.models import CloudAccount
from app.routers import credentials as credentials_router
from app.providers import VerificationResult
from tests.test_costs_api import _add_cost_row
from tests.test_resources_api import _make_account, _make_credential, _make_resource, _make_service

CONFIRM = {"X-Action-Confirmed": "true"}


def _seed(db_session, user):
    """AWS 계정 둘 — keep은 키가 남고, gone은 키를 지울 계정. 각자 리소스 1개·비용 1행."""
    service = _make_service(db_session, "aws", "ec2")
    out = {}
    for label, ext in (("keep", "111122223333"), ("gone", "444455556666")):
        account = _make_account(db_session, user, "aws", ext, label)
        credential = _make_credential(db_session, account, name=f"{label}-cred")
        resource = _make_resource(db_session, account, service, f"i-{label}")
        _add_cost_row(db_session, account, dt.date(2026, 9, 10), "10.000000")
        out[label] = (account, credential, resource)
    db_session.commit()
    return out


def _delete_credential(client, headers, credential):
    resp = client.delete(f"/api/v1/credentials/{credential.id}", headers={**headers, **CONFIRM})
    assert resp.status_code == 204


def test_deleting_last_credential_hides_account_everywhere(client, make_user, auth_header, db_session):
    user = make_user()
    headers = auth_header(user)
    seeded = _seed(db_session, user)
    gone_account, gone_cred, gone_resource = seeded["gone"]

    _delete_credential(client, headers, gone_cred)

    # 계정 행과 이력은 DB에 그대로 남는다 — 숨기기만 한다.
    assert db_session.get(CloudAccount, gone_account.id) is not None

    accounts = client.get("/api/v1/cloud-accounts", headers=headers).json()["data"]["items"]
    assert [a["external_account_id"] for a in accounts] == ["111122223333"]

    items = client.get("/api/v1/resources", headers=headers).json()["data"]["items"]
    assert [i["external_resource_id"] for i in items] == ["i-keep"]
    assert client.get(f"/api/v1/resources/{gone_resource.id}", headers=headers).status_code == 404

    caps = client.get("/api/v1/costs/capabilities", headers=headers).json()["data"]
    assert caps["total"] == 1

    teams = client.get("/api/v1/teams", headers=headers).json()["data"]
    assert teams["unassigned"]["account_count"] == 1


def test_explicit_orphan_account_id_is_not_found(client, make_user, auth_header, db_session):
    user = make_user()
    headers = auth_header(user)
    seeded = _seed(db_session, user)
    gone_account, gone_cred, _ = seeded["gone"]
    _delete_credential(client, headers, gone_cred)

    resp = client.post(
        "/api/v1/cost-ingestion-runs",
        json={"cloud_account_ids": [str(gone_account.id)], "period_start": "2026-09-01", "period_end": "2026-09-02"},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CLOUD_ACCOUNT_NOT_FOUND"


def test_reregistering_same_account_restores_history(client, make_user, auth_header, db_session, monkeypatch):
    user = make_user()
    headers = auth_header(user)
    seeded = _seed(db_session, user)
    gone_account, gone_cred, _ = seeded["gone"]
    _delete_credential(client, headers, gone_cred)

    monkeypatch.setattr(
        credentials_router, "verify_credential",
        lambda provider, ext, payload: VerificationResult(verified=True, permission_scope={}),
    )
    resp = client.post("/api/v1/credentials/aws", json={
        "external_account_id": "444455556666", "account_label": "gone", "name": "again",
        "secret_payload": {"access_key_id": "AKIAFAKE", "secret_access_key": "fake"},
    }, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["data"]["cloud_account_id"] == str(gone_account.id)  # 새 계정 행을 만들지 않는다

    items = client.get("/api/v1/resources", headers=headers).json()["data"]["items"]
    assert sorted(i["external_resource_id"] for i in items) == ["i-gone", "i-keep"]


def test_unverified_credential_still_counts_as_registered(client, make_user, auth_header, db_session):
    """검증 실패한 키도 마이페이지에 보이는 등록된 키다 — 계정을 숨기지 않는다."""
    user = make_user()
    headers = auth_header(user)
    service = _make_service(db_session, "aws", "ec2")
    account = _make_account(db_session, user, "aws", "111122223333")
    _make_credential(db_session, account, verified=False)
    _make_resource(db_session, account, service, "i-unverified")
    db_session.commit()

    items = client.get("/api/v1/resources", headers=headers).json()["data"]["items"]
    assert [i["external_resource_id"] for i in items] == ["i-unverified"]
