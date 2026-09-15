"""GET /credentials/aws/delegation-setup — 역할 위임 온보딩 정보 발급."""

from __future__ import annotations

import pytest

from app.config import get_settings

SETUP_URL = "/api/v1/credentials/aws/delegation-setup"


@pytest.fixture()
def platform_configured(monkeypatch):
    monkeypatch.setenv("PLATFORM_AWS_ACCOUNT_ID", "111122223333")
    monkeypatch.setenv("PLATFORM_AWS_ASSUMABLE_ROLE_PATTERN", "arn:aws:iam::*:role/MultiCloudOps*")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _setup(client, headers):
    response = client.get(SETUP_URL, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_requires_authentication(client):
    assert client.get(SETUP_URL).status_code == 401


def test_returns_trust_policy_with_platform_account(client, make_user, auth_header, platform_configured):
    data = _setup(client, auth_header(make_user()))

    statement = data["trust_policy"]["Statement"][0]
    # Principal은 계정 root — IAM 사용자 ARN을 쓰면 그 사용자를 재생성했을 때 고객 쪽 신뢰가
    # 전부 깨진다.
    assert statement["Principal"]["AWS"] == "arn:aws:iam::111122223333:root"
    assert statement["Action"] == "sts:AssumeRole"
    assert statement["Condition"]["StringEquals"]["sts:ExternalId"] == data["external_id"]


def test_role_name_prefix_is_derived_from_pattern(client, make_user, auth_header, platform_configured):
    data = _setup(client, auth_header(make_user()))

    assert data["role_name_prefix"] == "MultiCloudOps"
    assert data["suggested_role_name"] == "MultiCloudOpsAccess"


def test_external_id_is_fresh_each_request(client, make_user, auth_header, platform_configured):
    headers = auth_header(make_user())

    first = _setup(client, headers)["external_id"]
    second = _setup(client, headers)["external_id"]

    # 서버에 보관하지 않으므로 매번 새로 발급된다 — 사용자가 신뢰 정책과 등록 요청에 같은 값을
    # 쓰기만 하면 되는 구조다.
    assert first != second


def test_troubleshooting_covers_all_access_denied_causes(client, make_user, auth_header, platform_configured):
    data = _setup(client, auth_header(make_user()))

    joined = " ".join(data["troubleshooting"])
    # STS는 셋 중 무엇이 틀려도 같은 AccessDenied를 주므로 전부 안내해야 한다.
    assert "역할 이름" in joined
    assert "신뢰 정책" in joined
    assert "ExternalId" in joined


def test_503_when_platform_account_not_configured(client, make_user, auth_header, monkeypatch):
    monkeypatch.setenv("PLATFORM_AWS_ACCOUNT_ID", "")
    get_settings.cache_clear()

    response = client.get(SETUP_URL, headers=auth_header(make_user()))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PLATFORM_AWS_NOT_CONFIGURED"
