"""테스트 시드 공용 도우미.

자격 증명이 하나도 없는 클라우드 계정은 조회에서 빠진다(`app/account_scope.py`). 실제로 화면에
보이는 계정은 항상 credential을 1개 이상 갖고 있으므로, 계정만 심는 테스트는 이 함수로
"등록된 계정" 모양을 맞춘다.

기본값은 **미검증**이다 — 존재 자체만 필요하고, 검증된 키가 있다는 사실이 수집·액션 같은
다른 판정에 끼어들지 않게 한다.
"""

from app.models import Credential
from app.security.credential_crypto import encrypt_credential_json


def add_credential(db, account, *, name="fixture-cred", verified=False, permission_scope=None):
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    row = Credential(
        cloud_account_id=account.id,
        name=name,
        encrypted_payload=ciphertext,
        encryption_nonce=nonce,
        encryption_key_version="v1",
        verified=verified,
        permission_scope=permission_scope or {},
    )
    db.add(row)
    db.flush()
    return row
