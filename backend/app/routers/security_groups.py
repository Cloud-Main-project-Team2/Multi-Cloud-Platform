"""보안그룹 관리(2026-09-17) — 프로비저닝에서 EC2/RDS 등을 생성할 때 자동으로 만들어지는
보안그룹과 별개로, 사용자가 직접 AWS Security Group / Azure NSG / GCP 방화벽 규칙을
생성·조회·삭제하고 규칙을 추가·삭제하는 화면.

`credentials.py`의 `GET /credentials/{id}/network-resources`(조회 전용, id/name 요약)와
다르다 — 이건 규칙까지 포함한 상세를 다루고 실제로 만들고 지운다. 다만 소유권 확인·delegation
처리(`resolve_secret_payload`)는 그 라우터와 동일한 패턴을 그대로 쓴다.

**DB에 저장하지 않는다** — `list_network_resources`와 같은 원칙으로 매 요청마다 CSP를 직접
조회/변경한다(설계 근거는 `docs/Security_Group_Management_Design_2026-09-17.md` §1 참고).
그래서 `audit_events`도 남기지 않는다(§14 표에 없는 action이고, 이 기능은 DB에 target_id로
쓸 만한 행이 애초에 없다 — resource-sync가 같은 이유로 감사 로그를 안 남기는 전례를 따른다).

**GCP는 "그룹"이 없다** — 방화벽 규칙 자체가 최상위 객체라, `.../rules` 서브 엔드포인트 2개는
GCP 크리덴셜로 호출하면 422로 거부한다. `POST .../security-groups` 한 번으로 완성된 규칙을
만든다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_confirmation
from app.errors import ApiError, validation_error
from app.models import CloudAccount, Credential, User
from app.providers import aws as aws_provider
from app.providers import azure as azure_provider
from app.providers import gcp as gcp_provider
from app.providers.session import CredentialResolutionError, resolve_secret_payload
from app.resource_actions import ResourceActionError, fill_message_from_cause
from app.schemas.security_groups import (
    SecurityGroupCreateRequest,
    SecurityGroupListData,
    SecurityGroupListResponse,
    SecurityGroupOut,
    SecurityGroupResponse,
    SecurityGroupRuleCreateRequest,
    SecurityGroupRuleOut,
    SecurityGroupRuleResponse,
)
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json

router = APIRouter(prefix="/api/v1", tags=["security-groups"])


# --- 소유권 확인 (credentials.py의 _get_owned_credential과 동일 패턴, 라우터별 자체 헬퍼 관례를 따름) ---


def _parse_id(raw: str, not_found_code: str, not_found_message: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(404, not_found_code, not_found_message) from exc


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


def _resolve_credential(db: Session, current_user: User, credential_id: str, region: str | None):
    """소유권 확인 → 검증 여부 확인 → AWS region 필수 확인 → 복호화 → delegation 해석까지
    5개 엔드포인트가 전부 반복하는 앞부분을 한곳에 모은다(`credentials.py`의
    `get_network_resources`와 동일 순서)."""
    credential, account = _get_owned_credential(db, current_user.id, credential_id)

    if not credential.verified:
        raise ApiError(422, "CLOUD_PERMISSION_DENIED", "검증된 자격 증명이 아닙니다 — 먼저 재검증하세요.")

    if account.provider == "aws" and not region:
        raise validation_error(
            "AWS는 조회할 region이 필요합니다.", details=[{"field": "region", "reason": "required"}]
        )

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        raise ApiError(422, "PROVIDER_API_ERROR", "자격 증명을 복호화하지 못했습니다.")

    try:
        secret_payload = resolve_secret_payload(account.provider, secret_payload, credential_id=credential.id)
    except CredentialResolutionError as exc:
        raise ApiError(422, exc.error_code, exc.message or "임시 자격 증명을 발급받지 못했습니다.")

    return account, secret_payload


def _require_gcp_group_support(account: CloudAccount) -> None:
    if account.provider == "gcp":
        raise validation_error(
            "GCP는 방화벽 규칙 자체가 최상위 객체라 규칙 추가/삭제 API가 없습니다 — "
            "POST/DELETE .../security-groups로 규칙 전체를 직접 만들거나 지우세요.",
            details=[{"field": "provider", "reason": "gcp_has_no_rule_subresource"}],
        )


def _require_resource_group(account: CloudAccount, resource_group: str | None) -> str:
    """Azure는 NSG 이름만으로 조작할 수 없다(같은 구독 안에서도 리소스 그룹마다 별도
    네임스페이스) — Azure ARM 리소스 ID 전체를 URL 경로에 넣으면 `/`가 섞여
    `.../rules/{rule_id}` 같은 하위 경로와 라우팅이 충돌하므로, 이름과 리소스 그룹을 별도
    파라미터로 받는다(`group_id`=NSG 이름, `resource_group`=쿼리 파라미터).
    `list_network_resources`가 이미 `network_security_groups[].resource_group`을 내려주므로
    프론트가 새로 조회할 필요는 없다."""
    if account.provider == "azure" and not resource_group:
        raise validation_error(
            "Azure는 resource_group 쿼리 파라미터가 필요합니다.",
            details=[{"field": "resource_group", "reason": "required"}],
        )
    return resource_group or ""


def _error_message(exc: ResourceActionError, secret_payload: dict, fallback: str) -> str:
    """SDK 예외 원문(redact됨)이 있으면 그걸 쓴다 — `app.main._error_body`가 이 원문에서
    "권한이 부족합니다..." 같은 구체 원인을 자동으로 뽑아 보여준다(2026-09-17,
    `app.error_patterns.translate_reason`). 원문이 없으면(사전 검사 실패 등) 고정 안내문으로
    대체한다."""
    fill_message_from_cause(exc, secret_payload)
    return exc.message or fallback


# --- 목록/단건 조회 --------------------------------------------------------------------


@router.get("/credentials/{credential_id}/security-groups", response_model=SecurityGroupListResponse)
def list_security_groups(
    credential_id: str,
    region: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SecurityGroupListResponse:
    account, secret_payload = _resolve_credential(db, current_user, credential_id, region)
    try:
        if account.provider == "aws":
            items = aws_provider.list_security_groups(secret_payload, region)
        elif account.provider == "azure":
            items = azure_provider.list_security_groups(secret_payload, account.external_account_id)
        else:
            items = gcp_provider.list_firewall_rules(secret_payload, account.external_account_id)
    except ResourceActionError as exc:
        raise ApiError(502, exc.code, _error_message(exc, secret_payload, "클라우드에서 보안그룹 목록을 가져오지 못했습니다.")) from exc
    finally:
        del secret_payload

    return SecurityGroupListResponse(data=SecurityGroupListData(items=items))


@router.get("/credentials/{credential_id}/security-groups/{group_id}", response_model=SecurityGroupResponse)
def get_security_group(
    credential_id: str,
    group_id: str,
    region: str | None = Query(default=None),
    resource_group: str | None = Query(default=None, description="Azure 전용"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SecurityGroupResponse:
    """단건 상세는 별도 SDK 호출을 새로 만들지 않고 목록에서 찾는다 — 3사 모두 목록 API가
    이미 규칙까지 포함한 전체 상세를 주기 때문에(§4) 단건 전용 API를 따로 둘 이유가 없다.

    `group_id`는 AWS는 SG ID, GCP는 방화벽 규칙 이름, **Azure는 NSG 이름**이다(ARM 리소스 ID
    전체가 아니다 — `/`가 섞이면 `.../rules/{rule_id}` 하위 경로와 라우팅이 충돌한다). Azure는
    이름이 리소스 그룹마다 별도 네임스페이스라 `resource_group` 쿼리 파라미터로 함께 좁힌다."""
    account, secret_payload = _resolve_credential(db, current_user, credential_id, region)
    _require_resource_group(account, resource_group)
    try:
        if account.provider == "aws":
            items = aws_provider.list_security_groups(secret_payload, region)
            match = next((g for g in items if g["id"] == group_id), None)
            key = "aws"
        elif account.provider == "azure":
            items = azure_provider.list_security_groups(secret_payload, account.external_account_id)
            match = next(
                (g for g in items if g["name"] == group_id and g["resource_group"] == resource_group), None
            )
            key = "azure"
        else:
            items = gcp_provider.list_firewall_rules(secret_payload, account.external_account_id)
            match = next((g for g in items if g["name"] == group_id), None)
            key = "gcp"
    except ResourceActionError as exc:
        raise ApiError(502, exc.code, _error_message(exc, secret_payload, "클라우드에서 보안그룹 목록을 가져오지 못했습니다.")) from exc
    finally:
        del secret_payload

    if match is None:
        raise ApiError(404, "SECURITY_GROUP_NOT_FOUND", "보안그룹을 찾을 수 없습니다.")
    return SecurityGroupResponse(data=SecurityGroupOut(**{key: match}))


# --- 생성/삭제 --------------------------------------------------------------------------


@router.post("/credentials/{credential_id}/security-groups", response_model=SecurityGroupResponse, status_code=201)
def create_security_group(
    credential_id: str,
    body: SecurityGroupCreateRequest,
    region: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> SecurityGroupResponse:
    account, secret_payload = _resolve_credential(db, current_user, credential_id, region)
    try:
        if account.provider == "aws":
            if body.aws is None:
                raise validation_error("AWS 보안그룹 생성에는 aws 필드가 필요합니다.")
            created = aws_provider.create_security_group(
                secret_payload, region, body.aws.name, body.aws.description, body.aws.vpc_id
            )
            key = "aws"
        elif account.provider == "azure":
            if body.azure is None:
                raise validation_error("Azure NSG 생성에는 azure 필드가 필요합니다.")
            created = azure_provider.create_security_group(
                secret_payload,
                account.external_account_id,
                body.azure.resource_group,
                body.azure.name,
                body.azure.location,
            )
            key = "azure"
        else:
            if body.gcp is None:
                raise validation_error("GCP 방화벽 규칙 생성에는 gcp 필드가 필요합니다.")
            created = gcp_provider.create_firewall_rule(secret_payload, account.external_account_id, body.gcp.model_dump())
            key = "gcp"
    except ResourceActionError as exc:
        raise ApiError(502, exc.code, _error_message(exc, secret_payload, "클라우드에 보안그룹을 만들지 못했습니다.")) from exc
    finally:
        del secret_payload

    return SecurityGroupResponse(data=SecurityGroupOut(**{key: created}))


@router.delete("/credentials/{credential_id}/security-groups/{group_id}", status_code=204)
def delete_security_group(
    credential_id: str,
    group_id: str,
    region: str | None = Query(default=None),
    resource_group: str | None = Query(default=None, description="Azure 전용"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> Response:
    account, secret_payload = _resolve_credential(db, current_user, credential_id, region)
    _require_resource_group(account, resource_group)
    try:
        if account.provider == "aws":
            aws_provider.delete_security_group(secret_payload, region, group_id)
        elif account.provider == "azure":
            azure_provider.delete_security_group(
                secret_payload, account.external_account_id, resource_group, group_id
            )
        else:
            # GCP는 "그룹 삭제"가 곧 "방화벽 규칙 삭제"다 — 별도 개념이 없다(§2).
            gcp_provider.delete_firewall_rule(secret_payload, account.external_account_id, group_id)
    except ResourceActionError as exc:
        raise ApiError(502, exc.code, _error_message(exc, secret_payload, "클라우드에서 보안그룹을 삭제하지 못했습니다.")) from exc
    finally:
        del secret_payload
    return Response(status_code=204)


# --- 규칙 추가/삭제 (AWS/Azure 전용) -----------------------------------------------------


@router.post(
    "/credentials/{credential_id}/security-groups/{group_id}/rules",
    response_model=SecurityGroupRuleResponse,
    status_code=201,
)
def add_security_group_rule(
    credential_id: str,
    group_id: str,
    body: SecurityGroupRuleCreateRequest,
    region: str | None = Query(default=None),
    resource_group: str | None = Query(default=None, description="Azure 전용"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> SecurityGroupRuleResponse:
    account, secret_payload = _resolve_credential(db, current_user, credential_id, region)
    _require_gcp_group_support(account)
    _require_resource_group(account, resource_group)
    try:
        if account.provider == "aws":
            if body.aws is None:
                raise validation_error("AWS 규칙 추가에는 aws 필드가 필요합니다.")
            rule = body.aws
            created = aws_provider.add_security_group_rule(
                secret_payload, region, group_id, rule.direction, rule.protocol,
                rule.from_port, rule.to_port, rule.cidr, rule.description,
            )
            key = "aws"
        else:
            if body.azure is None:
                raise validation_error("Azure 규칙 추가에는 azure 필드가 필요합니다.")
            created = azure_provider.add_security_group_rule(
                secret_payload, account.external_account_id, resource_group, group_id, body.azure.model_dump()
            )
            key = "azure"
    except ResourceActionError as exc:
        raise ApiError(502, exc.code, _error_message(exc, secret_payload, "클라우드에 규칙을 추가하지 못했습니다.")) from exc
    finally:
        del secret_payload

    return SecurityGroupRuleResponse(data=SecurityGroupRuleOut(**{key: created}))


@router.delete("/credentials/{credential_id}/security-groups/{group_id}/rules/{rule_id}", status_code=204)
def remove_security_group_rule(
    credential_id: str,
    group_id: str,
    rule_id: str,
    region: str | None = Query(default=None),
    resource_group: str | None = Query(default=None, description="Azure 전용"),
    direction: str | None = Query(default=None, description="AWS 전용 — ingress|egress"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _confirmed: None = Depends(require_confirmation),
) -> Response:
    account, secret_payload = _resolve_credential(db, current_user, credential_id, region)
    _require_gcp_group_support(account)
    _require_resource_group(account, resource_group)
    try:
        if account.provider == "aws":
            if direction not in ("ingress", "egress"):
                raise validation_error(
                    "AWS 규칙 삭제에는 direction(ingress|egress) 쿼리 파라미터가 필요합니다.",
                    details=[{"field": "direction", "reason": "required"}],
                )
            aws_provider.remove_security_group_rule(secret_payload, region, group_id, direction, rule_id)
        else:
            azure_provider.remove_security_group_rule(
                secret_payload, account.external_account_id, resource_group, group_id, rule_id
            )
    except ResourceActionError as exc:
        raise ApiError(502, exc.code, _error_message(exc, secret_payload, "클라우드에서 규칙을 삭제하지 못했습니다.")) from exc
    finally:
        del secret_payload
    return Response(status_code=204)
