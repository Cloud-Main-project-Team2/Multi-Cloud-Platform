"""POST /credentials/{provider} — docs/01_API_명세서_v1.1.md 6.2절.

CSP 실제 검증(예: Azure AD로 서비스 프린시펄 토큰 발급 시도)은 아직 구현하지 않았다 —
스펙 1.2절/19절에서 "검증 실패 시 저장 또는 rollback" 정책이 미확정이라고 명시한 대로,
이 구현은 옵션 1(실패해도 암호화 저장하고 `verified=false`)을 택했다. 실제 provider
검증 어댑터가 생기면 여기서 `credential.verify` 흐름을 이어붙이면 된다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import AuditEvent, CloudAccount, Credential
from app.schemas.credentials import CredentialCreateRequest
from app.security.auth import get_current_user_id
from app.security.credential_crypto import encrypt_credential_json
from app.services.provisioning.validation import find_secret_field

router = APIRouter(prefix="/api/v1", tags=["credentials"])

SUPPORTED_PROVIDERS = ("aws", "azure", "gcp")


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


def _mask(identifier: str | None) -> str | None:
    if not identifier:
        return None
    return identifier[:4] + "•" * max(len(identifier) - 4, 0)


def _credential_response(credential: Credential) -> dict:
    return {
        "data": {
            "id": str(credential.id),
            "cloud_account_id": str(credential.cloud_account_id),
            "name": credential.name,
            "masked_public_identifier": _mask(credential.public_identifier),
            "permission_scope": credential.permission_scope,
            "verified": credential.verified,
            "verified_at": _iso(credential.verified_at),
            "tags": credential.tags,
            "display_order": credential.display_order,
            "created_at": _iso(credential.created_at),
            "updated_at": _iso(credential.updated_at),
        }
    }


@router.post("/credentials/{provider}", status_code=201)
def create_credential(
    provider: str,
    body: CredentialCreateRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    if provider not in SUPPORTED_PROVIDERS:
        raise ApiError(422, "VALIDATION_ERROR", f"지원하지 않는 provider입니다: {provider}")

    secret_field = find_secret_field(body.tags or {})
    if secret_field is not None:
        raise ApiError(422, "SECRET_FIELD_NOT_ALLOWED", f"'{secret_field}' 필드는 tags에 넣을 수 없습니다.")

    account = db.execute(
        select(CloudAccount).where(
            CloudAccount.user_id == user_id,
            CloudAccount.provider == provider,
            CloudAccount.external_account_id == body.external_account_id,
        )
    ).scalar_one_or_none()

    account_created = False
    if account is None:
        account = CloudAccount(
            user_id=user_id,
            provider=provider,
            external_account_id=body.external_account_id,
            account_label=body.account_label,
        )
        db.add(account)
        db.flush()  # account.id 확보
        account_created = True

    existing_credential = db.execute(
        select(Credential).where(Credential.cloud_account_id == account.id, Credential.name == body.name)
    ).scalar_one_or_none()
    if existing_credential is not None:
        raise ApiError(409, "CREDENTIAL_ALREADY_EXISTS", "같은 이름의 credential이 이미 있습니다.")

    ciphertext, nonce = encrypt_credential_json(body.secret_payload)

    credential = Credential(
        cloud_account_id=account.id,
        name=body.name,
        encrypted_payload=ciphertext,
        encryption_nonce=nonce,
        encryption_key_version=get_settings().credential_encryption_key_version,
        public_identifier=body.public_identifier,
        tags=body.tags or {},
        display_order=body.display_order or 0,
        verified=False,
    )
    db.add(credential)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "CREDENTIAL_ALREADY_EXISTS", "같은 이름의 credential이 이미 있습니다.") from exc

    if account_created:
        db.add(
            AuditEvent(
                actor_user_id=user_id,
                action="cloud_account.create",
                target_type="cloud_account",
                target_id=str(account.id),
                result="success",
                provider=provider,
            )
        )
    db.add(
        AuditEvent(
            actor_user_id=user_id,
            action="credential.create",
            target_type="credential",
            target_id=str(credential.id),
            result="success",
            provider=provider,
            metadata_json={"cloud_account_id": str(account.id)},
        )
    )
    db.commit()

    return _credential_response(credential)
