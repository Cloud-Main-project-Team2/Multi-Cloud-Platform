"""auth_type 병존 + AWS 역할 위임 임시 자격증명 발급(`app/providers/session.py`).

이 테스트의 최우선 목적은 **기존 credential이 하나도 안 바뀐다는 것을 고정하는 것**이다.
`auth_type`이 없는 payload(= 지금 DB에 들어 있는 모든 credential, `seed_mock_data.py`의 목업
포함)는 검증 규칙도 그대로고 `resolve_secret_payload()`를 통과해도 값이 그대로 나와야 한다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.exceptions import ClientError

from app.config import get_settings
from app.errors import ApiError
from app.providers import (
    AUTH_TYPE_ACCESS_KEY,
    AUTH_TYPE_ASSUME_ROLE,
    auth_type_of,
    validate_secret_payload,
)
from app.providers import session as provider_session

LEGACY_AWS = {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "secret"}
LEGACY_AZURE = {"client_id": "c", "client_secret": "s", "tenant_id": "t"}
LEGACY_GCP = {
    "type": "service_account",
    "client_email": "sa@example.iam.gserviceaccount.com",
    "private_key_id": "kid",
    "private_key": "-----BEGIN PRIVATE KEY-----",
    "token_uri": "https://oauth2.googleapis.com/token",
}
DELEGATED_AWS = {
    "auth_type": AUTH_TYPE_ASSUME_ROLE,
    "role_arn": "arn:aws:iam::123456789012:role/MultiCloudOpsAccess",
    "external_id": "ext-123",
}


# --- 레거시 경로 회귀 방지 -------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,payload",
    [("aws", LEGACY_AWS), ("azure", LEGACY_AZURE), ("gcp", LEGACY_GCP)],
)
def test_legacy_payload_is_still_valid(provider, payload):
    """auth_type이 없는 기존 payload는 3사 모두 그대로 통과한다."""
    assert auth_type_of(payload) == AUTH_TYPE_ACCESS_KEY
    validate_secret_payload(provider, payload)


@pytest.mark.parametrize(
    "provider,payload",
    [("aws", LEGACY_AWS), ("azure", LEGACY_AZURE), ("gcp", LEGACY_GCP)],
)
def test_legacy_payload_passes_through_resolve(provider, payload):
    """레거시는 resolve를 거쳐도 값이 그대로다 — STS를 호출하지 않는다."""
    assert provider_session.resolve_secret_payload(provider, payload) is payload


def test_legacy_missing_field_still_rejected():
    with pytest.raises(ApiError) as exc:
        validate_secret_payload("aws", {"access_key_id": "AKIAEXAMPLE"})
    assert exc.value.code == "VALIDATION_ERROR"


# --- 위임 payload 검증 -----------------------------------------------------------------


def test_delegated_payload_is_valid():
    validate_secret_payload("aws", DELEGATED_AWS)


def test_delegated_payload_requires_external_id():
    payload = {"auth_type": AUTH_TYPE_ASSUME_ROLE, "role_arn": DELEGATED_AWS["role_arn"]}
    with pytest.raises(ApiError) as exc:
        validate_secret_payload("aws", payload)
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.details == [{"field": "secret_payload.external_id", "reason": "required"}]


def test_empty_string_counts_as_missing():
    """빈 문자열은 값이 없는 것으로 본다 — 폼에서 빈 칸으로 넘어오는 경우."""
    payload = {**DELEGATED_AWS, "role_arn": ""}
    with pytest.raises(ApiError):
        validate_secret_payload("aws", payload)


@pytest.mark.parametrize("provider", ["azure", "gcp"])
def test_assume_role_not_supported_for_azure_gcp(provider):
    """이번 범위에서 위임 방식은 AWS 전용이다."""
    with pytest.raises(ApiError) as exc:
        validate_secret_payload(provider, {"auth_type": AUTH_TYPE_ASSUME_ROLE})
    assert exc.value.details == [{"field": "secret_payload.auth_type", "reason": "unsupported"}]


# --- AssumeRole 호출 ------------------------------------------------------------------


class _FakeSTS:
    def __init__(self, *, error: ClientError | None = None):
        self.error = error
        self.calls: list[dict] = []

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {
            "Credentials": {
                "AccessKeyId": "ASIATEMP",
                "SecretAccessKey": "temp-secret",
                "SessionToken": "temp-token",
                "Expiration": dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc),
            }
        }


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "nope"}}, "AssumeRole")


@pytest.fixture
def fake_sts(monkeypatch):
    sts = _FakeSTS()
    monkeypatch.setattr(provider_session, "_platform_sts_client", lambda: sts)
    return sts


def test_resolve_returns_temporary_credentials(fake_sts):
    resolved = provider_session.resolve_secret_payload("aws", DELEGATED_AWS, credential_id=7)

    # 레거시와 같은 키 이름으로 돌려준다 — 하류(providers/aws.py, AWS 러너 4개)가
    # 두 방식을 구분할 필요가 없어야 한다.
    assert resolved["access_key_id"] == "ASIATEMP"
    assert resolved["secret_access_key"] == "temp-secret"
    assert resolved["session_token"] == "temp-token"
    assert resolved["expiration"] == "2026-09-15T12:00:00+00:00"


def test_assume_role_call_arguments(fake_sts):
    provider_session.resolve_secret_payload("aws", DELEGATED_AWS, credential_id=7)

    call = fake_sts.calls[0]
    assert call["RoleArn"] == DELEGATED_AWS["role_arn"]
    assert call["ExternalId"] == "ext-123"
    assert call["DurationSeconds"] == get_settings().platform_aws_session_duration_seconds
    # 세션 이름은 고객 계정 CloudTrail에 찍힌다 — 어느 credential인지 알아볼 수 있어야 한다.
    assert call["RoleSessionName"] == "multicloud-ops-7"


def test_access_denied_message_lists_all_three_causes(monkeypatch):
    sts = _FakeSTS(error=_client_error("AccessDenied"))
    monkeypatch.setattr(provider_session, "_platform_sts_client", lambda: sts)

    with pytest.raises(provider_session.CredentialResolutionError) as exc:
        provider_session.resolve_secret_payload("aws", DELEGATED_AWS)

    assert exc.value.error_code == "CLOUD_PERMISSION_DENIED"
    # STS가 원인을 구분해주지 않으므로 점검 항목 세 가지를 모두 안내해야 한다.
    assert "역할 이름" in exc.value.message
    assert "신뢰 정책" in exc.value.message
    assert "ExternalId" in exc.value.message


def test_platform_credential_problem_is_not_blamed_on_user(monkeypatch):
    sts = _FakeSTS(error=_client_error("InvalidClientTokenId"))
    monkeypatch.setattr(provider_session, "_platform_sts_client", lambda: sts)

    with pytest.raises(provider_session.CredentialResolutionError) as exc:
        provider_session.resolve_secret_payload("aws", DELEGATED_AWS)

    assert exc.value.error_code == "PROVIDER_AUTHENTICATION_FAILED"
    assert "관리자" in exc.value.message


def test_delegated_payload_missing_fields_raises_resolution_error(fake_sts):
    """검증을 우회해 DB에 들어간 값이라도 STS를 호출하지 않고 막는다."""
    with pytest.raises(provider_session.CredentialResolutionError) as exc:
        provider_session.resolve_secret_payload("aws", {"auth_type": AUTH_TYPE_ASSUME_ROLE})

    assert exc.value.error_code == "CREDENTIAL_VERIFICATION_FAILED"
    assert fake_sts.calls == []
