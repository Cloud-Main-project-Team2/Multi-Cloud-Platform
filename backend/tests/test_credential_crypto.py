import base64
import os

import pytest

from app.security import credential_crypto as cc

VALID_KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    cc.get_settings.cache_clear()
    yield
    cc.get_settings.cache_clear()


def test_round_trip(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", VALID_KEY)
    payload = {"access_key": "AKIAEXAMPLE", "secret_key": "s3cr3t-value"}

    ciphertext, nonce = cc.encrypt_credential_json(payload)

    assert cc.decrypt_credential_json(ciphertext, nonce) == payload


def test_same_plaintext_encrypts_differently_each_time(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", VALID_KEY)
    payload = {"a": 1}

    ciphertext1, nonce1 = cc.encrypt_credential_json(payload)
    ciphertext2, nonce2 = cc.encrypt_credential_json(payload)

    assert nonce1 != nonce2
    assert ciphertext1 != ciphertext2


def test_tampered_ciphertext_fails_to_decrypt(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", VALID_KEY)
    ciphertext, nonce = cc.encrypt_credential_json({"a": 1})
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]

    with pytest.raises(cc.CredentialEncryptionError):
        cc.decrypt_credential_json(tampered, nonce)


def test_missing_key_fails_safely(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "")

    with pytest.raises(cc.CredentialEncryptionError) as exc_info:
        cc.encrypt_credential_json({"secret_key": "should-not-leak"})

    assert "should-not-leak" not in str(exc_info.value)


def test_invalid_key_format_fails_safely(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "not-valid-base64!!")

    with pytest.raises(cc.CredentialEncryptionError):
        cc.encrypt_credential_json({"a": 1})


def test_wrong_length_key_fails_safely(monkeypatch):
    short_key = base64.b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", short_key)

    with pytest.raises(cc.CredentialEncryptionError):
        cc.encrypt_credential_json({"a": 1})
