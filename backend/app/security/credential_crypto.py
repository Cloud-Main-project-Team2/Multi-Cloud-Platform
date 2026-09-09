"""Application-layer AES-256-GCM encryption for cloud credential payloads.

Encryption keys and plaintext credential material must never appear in
exceptions, logs, or string representations raised from this module.
"""

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

NONCE_SIZE_BYTES = 12
KEY_SIZE_BYTES = 32


class CredentialEncryptionError(Exception):
    """Raised when a credential payload cannot be encrypted or decrypted."""


def _load_key() -> bytes:
    raw = get_settings().credential_encryption_key
    if not raw:
        raise CredentialEncryptionError("credential encryption key is not configured")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise CredentialEncryptionError("credential encryption key is not valid base64") from exc
    if len(key) != KEY_SIZE_BYTES:
        raise CredentialEncryptionError("credential encryption key must decode to 32 bytes")
    return key


def encrypt_credential_json(payload: dict) -> tuple[bytes, bytes]:
    """Serialize payload to UTF-8 JSON and encrypt it. Returns (ciphertext, nonce)."""
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    key = _load_key()
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return ciphertext, nonce


def decrypt_credential_json(ciphertext: bytes, nonce: bytes) -> dict:
    """Decrypt a payload produced by encrypt_credential_json and parse it as JSON."""
    key = _load_key()
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise CredentialEncryptionError("credential payload failed integrity verification") from exc
    return json.loads(plaintext.decode("utf-8"))
