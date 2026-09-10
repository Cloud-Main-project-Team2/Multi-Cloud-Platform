"""API 명세서 v1.1 §6 클라우드 계정·자격 증명 API.

마이페이지 "계정 정보" 화면의 3구역(연결된 클라우드 계정 표 / 키 & 플랫폼 IAM 계정 관리 /
계정별 credential 목록)에 대응한다. §15에 따라 모든 조회는 URL의 ID를 신뢰하지 않고
`credentials.cloud_account_id -> cloud_accounts.user_id` 관계를 따라가 소유권을 확인하며,
타 사용자 소유 ID는 404로 응답해 존재 여부를 노출하지 않는다.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user, require_confirmation
from app.errors import ApiError, confirmation_required, validation_error
from app.logging_config import log_business_event
from app.models import CloudAccount, Credential, ProvisioningJob, ResourceSyncJobItem, User
from app.providers import PROVIDERS, VerificationResult, validate_secret_payload, verify_credential
from app.schemas.credentials import (
    CloudAccountListData,
    CloudAccountListResponse,
    CloudAccountResponse,
    CreateCredentialRequest,
    CredentialListData,
    CredentialListResponse,
    CredentialOrderRequest,
    CredentialResponse,
    PatchCloudAccountRequest,
    PatchCredentialRequest,
    VerifyResponse,
    VerifyResponseData,
)
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json, encrypt_credential_json
from app.serialization import iso_z, mask_public_identifier, str_id

router = APIRouter(prefix="/api/v1", tags=["credentials"])


# --- 소유권 확인 헬퍼 -----------------------------------------------------------------


def _parse_id(raw: str, not_found_code: str, not_found_message: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(404, not_found_code, not_found_message) from exc


def _get_owned_cloud_account(db: Session, user_id: int, cloud_account_id: str) -> CloudAccount:
    account_id = _parse_id(cloud_account_id, "CLOUD_ACCOUNT_NOT_FOUND", "클라우드 계정을 찾을 수 없습니다.")
    account = db.get(CloudAccount, account_id)
    if account is None or account.user_id != user_id:
        raise ApiError(404, "CLOUD_ACCOUNT_NOT_FOUND", "클라우드 계정을 찾을 수 없습니다.")
    return account


def _get_owned_credential(db: Session, user_id: int, credential_id: str) -> tuple[Credential, CloudAccount]:
    cred_id = _parse_id(credential_id, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
    row = (
        db.query(Credential, CloudAccount)
        .join(CloudAccount, Credential.cloud_account_id == CloudAccount.id)
        .filter(Credential.id == cred_id, CloudAccount.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
    return row


# --- 직렬화 ---------------------------------------------------------------------------


def _serialize_cloud_account(account: CloudAccount) -> dict:
    return {
        "id": str_id(account.id),
        "provider": account.provider,
        "external_account_id": account.external_account_id,
        "account_label": account.account_label,
        "created_at": iso_z(account.created_at),
        "updated_at": iso_z(account.updated_at),
    }


def _serialize_credential(credential: Credential) -> dict:
    return {
        "id": str_id(credential.id),
        "cloud_account_id": str_id(credential.cloud_account_id),
        "name": credential.name,
        "masked_public_identifier": credential.public_identifier,
        "permission_scope": credential.permission_scope,
        "verified": credential.verified,
        "verified_at": iso_z(credential.verified_at),
        "tags": credential.tags,
        "display_order": credential.display_order,
        "created_at": iso_z(credential.created_at),
        "updated_at": iso_z(credential.updated_at),
    }


def _apply_verification(credential: Credential, verification: VerificationResult) -> None:
    credential.verified = verification.verified
    credential.verified_at = dt.datetime.now(dt.timezone.utc) if verification.verified else None
    credential.permission_scope = verification.permission_scope


def _verify_audit_metadata(verification: VerificationResult) -> dict | None:
    return {"error_code": verification.error_code} if verification.error_code else None


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# --- cloud accounts ---------------------------------------------------------------------


@router.get("/cloud-accounts", response_model=CloudAccountListResponse)
def list_cloud_accounts(
    provider: list[str] | None = Query(default=None),
    name: str | None = Query(default=None),
    verified: bool | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CloudAccountListResponse:
    query = db.query(CloudAccount).filter(CloudAccount.user_id == current_user.id)

    if provider:
        invalid = sorted(set(provider) - set(PROVIDERS))
        if invalid:
            raise validation_error(
                "지원하지 않는 provider입니다.",
                details=[{"field": "provider", "reason": value} for value in invalid],
            )
        query = query.filter(CloudAccount.provider.in_(provider))

    if name:
        pattern = f"%{name.strip()}%"
        matching_account_ids = (
            sa.select(Credential.cloud_account_id)
            .join(CloudAccount, Credential.cloud_account_id == CloudAccount.id)
            .where(CloudAccount.user_id == current_user.id, Credential.name.ilike(pattern))
        )
        query = query.filter(
            sa.or_(CloudAccount.account_label.ilike(pattern), CloudAccount.id.in_(matching_account_ids))
        )

    if verified is not None:
        verified_account_ids = (
            sa.select(Credential.cloud_account_id)
            .join(CloudAccount, Credential.cloud_account_id == CloudAccount.id)
            .where(CloudAccount.user_id == current_user.id, Credential.verified == verified)
        )
        query = query.filter(CloudAccount.id.in_(verified_account_ids))

    accounts = query.order_by(CloudAccount.id).all()
    items = [_serialize_cloud_account(a) for a in accounts]
    return CloudAccountListResponse(data=CloudAccountListData(items=items, total=len(items)))


@router.get("/cloud-accounts/{cloud_account_id}", response_model=CloudAccountResponse)
def get_cloud_account(
    cloud_account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CloudAccountResponse:
    account = _get_owned_cloud_account(db, current_user.id, cloud_account_id)
    return CloudAccountResponse(data=_serialize_cloud_account(account))


@router.patch("/cloud-accounts/{cloud_account_id}", response_model=CloudAccountResponse)
def patch_cloud_account(
    cloud_account_id: str,
    payload: PatchCloudAccountRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CloudAccountResponse:
    account = _get_owned_cloud_account(db, current_user.id, cloud_account_id)
    if payload.account_label is not None:
        account.account_label = payload.account_label
    db.commit()
    db.refresh(account)
    return CloudAccountResponse(data=_serialize_cloud_account(account))


@router.delete("/cloud-accounts/{cloud_account_id}")
def delete_cloud_account(
    cloud_account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    # 운영 정책(§6.5, §19 "cloud account 삭제 시 snapshot 보존 정책")이 확정될 때까지
    # 구현을 보류한다 — CLAUDE.md에 결정을 기록해 뒀다. 소유권 확인만 정상 수행하고 501을 반환한다.
    _get_owned_cloud_account(db, current_user.id, cloud_account_id)
    raise ApiError(
        501,
        "CLOUD_ACCOUNT_DELETE_NOT_IMPLEMENTED",
        "클라우드 계정 삭제는 아직 보류 중입니다(snapshot 보존 정책 미확정).",
    )


@router.get("/cloud-accounts/{cloud_account_id}/credentials", response_model=CredentialListResponse)
def list_credentials_for_account(
    cloud_account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialListResponse:
    account = _get_owned_cloud_account(db, current_user.id, cloud_account_id)
    credentials = (
        db.query(Credential)
        .filter(Credential.cloud_account_id == account.id)
        .order_by(Credential.display_order, Credential.id)
        .all()
    )
    items = [_serialize_credential(c) for c in credentials]
    return CredentialListResponse(data=CredentialListData(items=items, total=len(items)))


# --- credentials -------------------------------------------------------------------------


@router.post("/credentials/{provider}", response_model=CredentialResponse, status_code=201)
def create_credential(
    provider: str,
    payload: CreateCredentialRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialResponse:
    if provider not in PROVIDERS:
        raise validation_error("지원하지 않는 provider입니다.", details=[{"field": "provider", "reason": "invalid"}])
    validate_secret_payload(provider, payload.secret_payload)

    account = (
        db.query(CloudAccount)
        .filter_by(user_id=current_user.id, provider=provider, external_account_id=payload.external_account_id)
        .one_or_none()
    )
    account_created = account is None
    if account is None:
        account = CloudAccount(
            user_id=current_user.id,
            provider=provider,
            external_account_id=payload.external_account_id,
            account_label=payload.account_label,
        )
        db.add(account)
        db.flush()

    duplicate = (
        db.query(Credential).filter_by(cloud_account_id=account.id, name=payload.name).one_or_none()
    )
    if duplicate is not None:
        raise ApiError(409, "CREDENTIAL_ALREADY_EXISTS", "같은 이름의 자격 증명이 이미 있습니다.")

    ciphertext, nonce = encrypt_credential_json(payload.secret_payload)
    credential = Credential(
        cloud_account_id=account.id,
        name=payload.name,
        encrypted_payload=ciphertext,
        encryption_nonce=nonce,
        encryption_key_version=get_settings().credential_encryption_key_version,
        public_identifier=mask_public_identifier(payload.public_identifier) if payload.public_identifier else None,
        tags=payload.tags,
        display_order=payload.display_order,
    )
    db.add(credential)
    db.flush()

    if account_created:
        record_audit_event(
            db,
            actor_user_id=current_user.id,
            action="cloud_account.create",
            target_type="cloud_account",
            target_id=str(account.id),
            result="success",
            provider=provider,
            request_id=_request_id(request),
        )
    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="credential.create",
        target_type="credential",
        target_id=str(credential.id),
        result="requested",
        provider=provider,
        request_id=_request_id(request),
    )

    # 결정(2026-09-10, 이 세션): 검증 성공/실패와 무관하게 credential은 저장하고 verified만 반영한다.
    # 근거는 CLAUDE.md 참고.
    verification = verify_credential(provider, payload.external_account_id, payload.secret_payload)
    _apply_verification(credential, verification)

    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="credential.verify",
        target_type="credential",
        target_id=str(credential.id),
        result="success" if verification.verified else "failure",
        provider=provider,
        metadata=_verify_audit_metadata(verification),
        request_id=_request_id(request),
    )
    log_business_event(
        "credential.verify", provider=provider, credential_id=credential.id,
        verified=verification.verified, error_code=verification.error_code,
    )

    db.commit()
    db.refresh(credential)
    return CredentialResponse(data=_serialize_credential(credential))


@router.patch("/credentials/{credential_id}", response_model=CredentialResponse)
def patch_credential(
    credential_id: str,
    payload: PatchCredentialRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialResponse:
    credential, account = _get_owned_credential(db, current_user.id, credential_id)

    replacing_secret = payload.secret_payload is not None
    if replacing_secret and request.headers.get("X-Action-Confirmed") != "true":
        raise confirmation_required()

    if payload.name is not None and payload.name != credential.name:
        duplicate = (
            db.query(Credential)
            .filter(
                Credential.cloud_account_id == account.id,
                Credential.name == payload.name,
                Credential.id != credential.id,
            )
            .one_or_none()
        )
        if duplicate is not None:
            raise ApiError(409, "CREDENTIAL_ALREADY_EXISTS", "같은 이름의 자격 증명이 이미 있습니다.")
        credential.name = payload.name

    if payload.tags is not None:
        credential.tags = payload.tags
    if payload.display_order is not None:
        credential.display_order = payload.display_order
    if payload.public_identifier is not None:
        credential.public_identifier = mask_public_identifier(payload.public_identifier)

    if replacing_secret:
        validate_secret_payload(account.provider, payload.secret_payload)
        ciphertext, nonce = encrypt_credential_json(payload.secret_payload)
        credential.encrypted_payload = ciphertext
        credential.encryption_nonce = nonce
        credential.encryption_key_version = get_settings().credential_encryption_key_version

        verification = verify_credential(account.provider, account.external_account_id, payload.secret_payload)
        _apply_verification(credential, verification)

        record_audit_event(
            db,
            actor_user_id=current_user.id,
            action="credential.verify",
            target_type="credential",
            target_id=str(credential.id),
            result="success" if verification.verified else "failure",
            provider=account.provider,
            metadata=_verify_audit_metadata(verification),
            request_id=_request_id(request),
        )
        log_business_event(
            "credential.verify", provider=account.provider, credential_id=credential.id,
            verified=verification.verified, error_code=verification.error_code,
        )

    db.commit()
    db.refresh(credential)
    return CredentialResponse(data=_serialize_credential(credential))


@router.post("/credentials/{credential_id}/verify", response_model=VerifyResponse)
def verify_credential_endpoint(
    credential_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VerifyResponse:
    credential, account = _get_owned_credential(db, current_user.id, credential_id)

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        verification = VerificationResult(verified=False, error_code="PROVIDER_API_ERROR")
    else:
        # 복호화 범위를 provider 호출 직전~직후로 최소화한다(§18).
        verification = verify_credential(account.provider, account.external_account_id, secret_payload)
        del secret_payload

    _apply_verification(credential, verification)

    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="credential.verify",
        target_type="credential",
        target_id=str(credential.id),
        result="success" if verification.verified else "failure",
        provider=account.provider,
        metadata=_verify_audit_metadata(verification),
        request_id=_request_id(request),
    )
    log_business_event(
        "credential.verify", provider=account.provider, credential_id=credential.id,
        verified=verification.verified, error_code=verification.error_code,
    )

    db.commit()
    return VerifyResponse(
        data=VerifyResponseData(
            credential_id=str_id(credential.id),
            verified=credential.verified,
            verified_at=iso_z(credential.verified_at),
            permission_scope=credential.permission_scope,
        )
    )


@router.delete("/credentials/{credential_id}", status_code=204)
def delete_credential(
    credential_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> Response:
    credential, account = _get_owned_credential(db, current_user.id, credential_id)

    active_provisioning = (
        db.query(ProvisioningJob)
        .filter(ProvisioningJob.credential_id == credential.id, ProvisioningJob.status.in_(["queued", "running"]))
        .first()
    )
    active_sync_item = (
        db.query(ResourceSyncJobItem)
        .filter(
            ResourceSyncJobItem.credential_id == credential.id,
            ResourceSyncJobItem.status.in_(["pending", "running"]),
        )
        .first()
    )
    if active_provisioning is not None or active_sync_item is not None:
        raise ApiError(409, "CREDENTIAL_IN_USE", "진행 중인 작업이 이 자격 증명을 참조하고 있습니다.")

    credential_id_str = str(credential.id)
    db.delete(credential)
    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="credential.delete",
        target_type="credential",
        target_id=credential_id_str,
        result="success",
        provider=account.provider,
        request_id=_request_id(request),
    )
    db.commit()
    return Response(status_code=204)


@router.put("/credentials/order", response_model=CredentialListResponse)
def reorder_credentials(
    payload: CredentialOrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialListResponse:
    raw_ids = [item.credential_id for item in payload.items]
    if len(set(raw_ids)) != len(raw_ids):
        raise validation_error("items에 중복된 credential_id가 있습니다.")

    ids: list[int] = []
    for raw_id in raw_ids:
        try:
            ids.append(int(raw_id))
        except ValueError as exc:
            raise ApiError(404, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.") from exc

    rows = (
        db.query(Credential)
        .join(CloudAccount, Credential.cloud_account_id == CloudAccount.id)
        .filter(Credential.id.in_(ids), CloudAccount.user_id == current_user.id)
        .all()
    )
    by_id = {c.id: c for c in rows}
    missing = set(ids) - set(by_id)
    if missing:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")

    for item in payload.items:
        by_id[int(item.credential_id)].display_order = item.display_order

    db.commit()

    order_by_id = {int(item.credential_id): item.display_order for item in payload.items}
    ordered = sorted(rows, key=lambda c: order_by_id[c.id])
    items = [_serialize_credential(c) for c in ordered]
    return CredentialListResponse(data=CredentialListData(items=items, total=len(items)))
