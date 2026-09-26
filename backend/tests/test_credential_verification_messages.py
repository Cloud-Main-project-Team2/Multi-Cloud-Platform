"""검증 실패 안내 문구가 **CSP별로** 나오는지 고정한다.

2026-09-26 이전에는 표가 하나뿐이라 Azure·GCP 자격증명이 실패해도 화면에 "AWS 인증에 실패했습니다"가
떴다(실사용에서 Azure·GCP 모두 확인). 역할 위임(AssumeRole)은 AWS에만 있는 개념이라 그 안내가 다른
CSP로 새어 나가서도 안 된다.

오류 **코드**(`verification_error_code`)는 이 변경 대상이 아니다 — 여전히 CSP와 무관하게 같다.
"""

from __future__ import annotations

import pytest

from app.models import CloudAccount, Credential
from app.providers import VerificationResult
import app.routers.credentials as credentials_router
from app.routers.credentials import _verification_message

GCP_SECRET = {
    "type": "service_account",
    "client_email": "svc@example.iam.gserviceaccount.com",
    "private_key_id": "key-id",
    "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    "token_uri": "https://oauth2.googleapis.com/token",
}
AZURE_SECRET = {
    "client_id": "11111111-2222-3333-4444-555555555555",
    "client_secret": "az-s3cr3t-should-never-leak",
    "tenant_id": "99999999-8888-7777-6666-555555555555",
}
AWS_SECRET = {"access_key_id": "AKIAFAKEKEY000001", "secret_access_key": "s3cr3t-should-never-leak"}
SECRETS = {"aws": AWS_SECRET, "azure": AZURE_SECRET, "gcp": GCP_SECRET}
EXTERNAL_IDS = {"aws": "111122223333", "azure": "00000000-1111-2222-3333-444455556666", "gcp": "demo-project"}
OTHER_NAMES = {"aws": ("Azure", "GCP"), "azure": ("AWS", "GCP"), "gcp": ("AWS", "Azure")}


def _mock_verify_failure(monkeypatch, error_code="PROVIDER_AUTHENTICATION_FAILED"):
    def _fake(provider, external_account_id, secret_payload):
        return VerificationResult(verified=False, error_code=error_code)

    monkeypatch.setattr(credentials_router, "verify_credential", _fake)


# --- 문구 표 자체 --------------------------------------------------------------------------------


@pytest.mark.parametrize("provider,expected", [("aws", "AWS"), ("azure", "Azure"), ("gcp", "GCP")])
def test_authentication_failed_names_the_right_csp(provider, expected):
    message = _verification_message("PROVIDER_AUTHENTICATION_FAILED", provider)

    assert expected in message
    for other in OTHER_NAMES[provider]:
        assert other not in message


def test_delegation_guidance_is_aws_only():
    """역할 위임은 AWS 개념이다 — 다른 CSP 문구에 섞이면 안 된다."""
    aws = _verification_message("CLOUD_PERMISSION_DENIED", "aws")
    assert "역할" in aws and "ExternalId" in aws

    for provider in ("azure", "gcp"):
        message = _verification_message("CLOUD_PERMISSION_DENIED", provider)
        assert "ExternalId" not in message and "신뢰 정책" not in message


def test_unknown_or_missing_provider_falls_back_without_a_csp_name():
    for provider in (None, "", "oracle"):
        message = _verification_message("PROVIDER_AUTHENTICATION_FAILED", provider)
        assert message == "자격 증명 검증에 실패했습니다."


def test_no_error_code_means_no_message():
    assert _verification_message(None, "azure") is None


# --- 실제 응답 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_create_credential_failure_message_matches_provider(client, make_user, auth_header, monkeypatch, provider):
    user = make_user()
    headers = auth_header(user)
    _mock_verify_failure(monkeypatch)

    resp = client.post(f"/api/v1/credentials/{provider}", json={
        "external_account_id": EXTERNAL_IDS[provider], "name": "cred", "secret_payload": SECRETS[provider],
    }, headers=headers)

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["verified"] is False
    assert data["verification_error_code"] == "PROVIDER_AUTHENTICATION_FAILED"   # 코드 계약은 그대로
    message = data["verification_error_message"]
    assert {"aws": "AWS", "azure": "Azure", "gcp": "GCP"}[provider] in message
    for other in OTHER_NAMES[provider]:
        assert other not in message


@pytest.mark.parametrize("provider", ["azure", "gcp"])
def test_reverify_failure_message_matches_provider(client, make_user, auth_header, monkeypatch, db_session, provider):
    """재검증(POST /credentials/{id}/verify) 응답도 같은 규칙을 따른다."""
    user = make_user()
    headers = auth_header(user)
    _mock_verify_failure(monkeypatch)
    resp = client.post(f"/api/v1/credentials/{provider}", json={
        "external_account_id": EXTERNAL_IDS[provider], "name": "cred", "secret_payload": SECRETS[provider],
    }, headers=headers)
    credential_id = resp.json()["data"]["id"]

    again = client.post(f"/api/v1/credentials/{credential_id}/verify", headers=headers)

    assert again.status_code == 200
    data = again.json()["data"]
    assert data["verification_error_code"] == "PROVIDER_AUTHENTICATION_FAILED"
    assert "AWS" not in data["verification_error_message"]
    assert {"azure": "Azure", "gcp": "GCP"}[provider] in data["verification_error_message"]


def test_listing_has_no_verification_message(client, make_user, auth_header, monkeypatch):
    """목록 조회는 검증을 수행하지 않으므로 사유도 없다(기존 계약)."""
    user = make_user()
    headers = auth_header(user)
    _mock_verify_failure(monkeypatch)
    client.post("/api/v1/credentials/azure", json={
        "external_account_id": EXTERNAL_IDS["azure"], "name": "cred", "secret_payload": AZURE_SECRET,
    }, headers=headers)

    accounts = client.get("/api/v1/cloud-accounts", headers=headers).json()["data"]["items"]
    listed = client.get(f"/api/v1/cloud-accounts/{accounts[0]['id']}/credentials", headers=headers)

    assert listed.status_code == 200
    for item in listed.json()["data"]["items"]:
        assert item["verification_error_code"] is None and item["verification_error_message"] is None
