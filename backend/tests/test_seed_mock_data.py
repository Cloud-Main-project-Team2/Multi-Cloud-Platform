"""목업 시딩 스크립트 동작 검증: idempotency + 검증실패 상태 + 암호화 왕복."""

import base64
import os

import pytest

from app.config import get_settings
from app.models import (
    CloudAccount,
    CloudResourceCost,
    Credential,
    Notification,
    ProvisioningJob,
    Resource,
    ResourceSyncJob,
    ResourceSyncJobItem,
    User,
)
from app.security.credential_crypto import decrypt_credential_json
from app.seed_mock_data import seed_mock_data

VALID_KEY = base64.b64encode(os.urandom(32)).decode()

EXPECTED_COUNTS = {
    User: 1,
    CloudAccount: 3,
    Credential: 3,
    Resource: 5,
    ProvisioningJob: 2,
    ResourceSyncJob: 1,
    ResourceSyncJobItem: 3,
    Notification: 2,
    CloudResourceCost: 4,
}


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", VALID_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_seed_mock_data_is_idempotent(db_session):
    seed_mock_data(db_session)
    seed_mock_data(db_session)  # 두 번 실행해도 중복 생성되지 않아야 한다
    db_session.flush()

    for model, expected in EXPECTED_COUNTS.items():
        assert db_session.query(model).count() == expected, model.__name__


def test_dev_gcp_credential_is_unverified(db_session):
    seed_mock_data(db_session)
    db_session.flush()

    creds = {c.name: c for c in db_session.query(Credential).all()}
    assert creds["dev-gcp-01-sa"].verified is False
    assert creds["prod-aws-01-key"].verified is True
    assert creds["prod-az-01-sp"].verified is True


def test_credentials_encrypted_payload_round_trips(db_session):
    seed_mock_data(db_session)
    db_session.flush()

    cred = db_session.query(Credential).filter_by(name="dev-gcp-01-sa").one()
    # 평문이 저장되면 안 된다.
    assert b"service_account" not in bytes(cred.encrypted_payload)

    decrypted = decrypt_credential_json(bytes(cred.encrypted_payload), bytes(cred.encryption_nonce))
    assert decrypted["type"] == "service_account"
    assert decrypted["client_email"].endswith("gserviceaccount.com")


def test_resources_match_screen_examples(db_session):
    seed_mock_data(db_session)
    db_session.flush()

    resources = {r.external_resource_id: r for r in db_session.query(Resource).all()}
    assert set(resources) == {
        "mcp-a1b2-vm", "mcp-c3d4-vm", "mcp-g7h8-vm", "mcp-a1b2-disk", "mcp-e5f6-sql",
    }
    # EBS Volume은 aws/ec2 카탈로그에 매핑하되 원본 유형은 보존한다.
    assert resources["mcp-a1b2-disk"].original_resource_type == "EBS Volume"
    assert resources["mcp-c3d4-vm"].status == "STOPPED"
