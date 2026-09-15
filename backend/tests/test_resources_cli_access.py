"""POST /resources/{id}/cli-access — SSH 키 페어 대신 STS 단기 자격증명으로 SSM Session Manager
접속을 발급하는 엔드포인트 검증(2026-09-15). 실제 AWS STS 호출은 하지 않는다 —
`app.providers.aws.issue_cli_session`을 monkeypatch해서 결정적으로 제어한다.
"""

from __future__ import annotations

import datetime as dt

from app.models import AuditEvent
from app.resource_actions import ResourceActionError

from tests.test_resources_api import _make_account, _make_credential, _make_resource, _make_service, _setup_aws_ec2

_NOW = dt.datetime.now(dt.timezone.utc)

_FAKE_SESSION = {
    "access_key_id": "ASIAFAKEKEYID",
    "secret_access_key": "fakeSecretAccessKey",
    "session_token": "fakeSessionToken",
    "expires_at": _NOW + dt.timedelta(minutes=15),
}


def _mock_issue_cli_session(monkeypatch, return_value=None, exc=None):
    import app.routers.resources as resources_router

    def _fake(secret_payload, **kwargs):
        if exc is not None:
            raise exc
        return return_value or _FAKE_SESSION

    monkeypatch.setattr(resources_router.aws_provider, "issue_cli_session", _fake)


def test_cli_access_requires_confirmation(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.post(f"/api/v1/resources/{resource.id}/cli-access", headers=auth_header(user))

    assert resp.status_code == 428


def test_cli_access_success_returns_temporary_credentials_and_records_audit(
    client, make_user, auth_header, db_session, monkeypatch
):
    _mock_issue_cli_session(monkeypatch)
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.post(
        f"/api/v1/resources/{resource.id}/cli-access",
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["access_key_id"] == _FAKE_SESSION["access_key_id"]
    assert data["secret_access_key"] == _FAKE_SESSION["secret_access_key"]
    assert data["session_token"] == _FAKE_SESSION["session_token"]
    assert data["instance_id"] == resource.external_resource_id
    assert data["region"] == resource.region
    assert "aws ssm start-session" in data["command"]
    assert resource.external_resource_id in data["command"]

    audit = db_session.query(AuditEvent).filter_by(target_type="resource", target_id=str(resource.id)).one()
    assert audit.action == "resource.cli_access"
    assert audit.result == "success"


def test_cli_access_rejects_non_aws_resource(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "azure", "sub-1")
    service = _make_service(db_session, "azure", "vm")
    _make_credential(db_session, account, verified=True)
    resource = _make_resource(db_session, account, service, "vm-1", original_resource_type="Virtual Machine")
    db_session.commit()

    resp = client.post(
        f"/api/v1/resources/{resource.id}/cli-access",
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_OPERATION"


def test_cli_access_rejects_ebs_volume(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    service = _make_service(db_session, "aws", "ec2")
    _make_credential(db_session, account, verified=True)
    volume = _make_resource(db_session, account, service, "vol-1", original_resource_type="EBS Volume")
    db_session.commit()

    resp = client.post(
        f"/api/v1/resources/{volume.id}/cli-access",
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_OPERATION"


def test_cli_access_rejects_already_deleted_resource(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    resource.deleted_at = _NOW
    db_session.commit()

    resp = client.post(
        f"/api/v1/resources/{resource.id}/cli-access",
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "RESOURCE_ALREADY_DELETED"


def test_cli_access_rejects_when_no_verified_credential(client, make_user, auth_header, db_session):
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user, verified=False)
    db_session.commit()

    resp = client.post(
        f"/api/v1/resources/{resource.id}/cli-access",
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CLOUD_PERMISSION_DENIED"


def test_cli_access_maps_provider_error_to_502(client, make_user, auth_header, db_session, monkeypatch):
    _mock_issue_cli_session(monkeypatch, exc=ResourceActionError("PROVIDER_API_ERROR"))
    user = make_user()
    _, _, _, resource = _setup_aws_ec2(db_session, user)
    db_session.commit()

    resp = client.post(
        f"/api/v1/resources/{resource.id}/cli-access",
        headers={**auth_header(user), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PROVIDER_API_ERROR"


def test_cli_access_not_found_for_other_user(client, make_user, auth_header, db_session):
    owner = make_user(email="cli-owner@example.com")
    intruder = make_user(email="cli-intruder@example.com")
    _, _, _, resource = _setup_aws_ec2(db_session, owner)
    db_session.commit()

    resp = client.post(
        f"/api/v1/resources/{resource.id}/cli-access",
        headers={**auth_header(intruder), "X-Action-Confirmed": "true"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
