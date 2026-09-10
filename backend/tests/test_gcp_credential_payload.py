"""GCP 서비스 계정 키 payload 계약 검증.

2026-09-11 실제로 겪은 버그: 프론트가 붙여넣은 JSON에서 4개 필드만 골라 보내는 바람에
`token_uri`가 빠졌고, google-auth의 `from_service_account_info()`가 `MalformedError`를 내면서
멀쩡한 키인데도 검증이 항상 실패했다. "우리가 필수로 받는 필드 집합"이 실제로 google-auth를
통과하기에 충분한지를 여기서 고정한다.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.oauth2 import service_account

from app.errors import ApiError
from app.providers import REQUIRED_SECRET_FIELDS, validate_secret_payload


@pytest.fixture(scope="module")
def service_account_json() -> dict:
    """실제 GCP 콘솔이 내려주는 서비스 계정 키 파일과 같은 형태(개인키는 테스트용 생성값)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account",
        "project_id": "demo-proj",
        "private_key_id": "abcdef0123456789",
        "private_key": pem,
        "client_email": "sa@demo-proj.iam.gserviceaccount.com",
        "client_id": "123456789012345678901",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def test_full_service_account_json_is_accepted(service_account_json):
    validate_secret_payload("gcp", service_account_json)  # raise하지 않으면 통과
    service_account.Credentials.from_service_account_info(service_account_json)


def test_required_fields_alone_are_enough_for_google_auth(service_account_json):
    """우리가 필수로 받는 필드만 있어도 google-auth가 credential을 만들 수 있어야 한다.

    이 테스트가 깨지면 REQUIRED_SECRET_FIELDS["gcp"]에서 google-auth가 필요로 하는 필드를
    빼먹었다는 뜻이다(정확히 이번 버그의 형태).
    """
    minimal = {k: service_account_json[k] for k in REQUIRED_SECRET_FIELDS["gcp"]}

    validate_secret_payload("gcp", minimal)
    service_account.Credentials.from_service_account_info(minimal)


def test_payload_without_token_uri_is_rejected_before_verification(service_account_json):
    without_token_uri = {k: v for k, v in service_account_json.items() if k != "token_uri"}

    with pytest.raises(ApiError) as exc_info:
        validate_secret_payload("gcp", without_token_uri)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert any(d["field"] == "secret_payload.token_uri" for d in exc_info.value.details)
