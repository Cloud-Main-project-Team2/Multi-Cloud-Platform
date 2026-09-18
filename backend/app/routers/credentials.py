"""API 명세서 v1.1 §6 클라우드 계정·자격 증명 API.

마이페이지 "계정 정보" 화면의 3구역(연결된 클라우드 계정 표 / 키 & 플랫폼 IAM 계정 관리 /
계정별 credential 목록)에 대응한다. §15에 따라 모든 조회는 URL의 ID를 신뢰하지 않고
`credentials.cloud_account_id -> cloud_accounts.user_id` 관계를 따라가 소유권을 확인하며,
타 사용자 소유 ID는 404로 응답해 존재 여부를 노출하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import uuid

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
from app.providers import (
    AUTH_TYPE_ACCESS_KEY,
    AUTH_TYPE_ASSUME_ROLE,
    PROVIDERS,
    VerificationResult,
    auth_type_of,
    validate_secret_payload,
    verify_credential,
)
from app.providers import aws as aws_provider
from app.providers import azure as azure_provider
from app.providers import gcp as gcp_provider
from app.providers.session import CredentialResolutionError, resolve_secret_payload
from app.resource_actions import ResourceActionError
from app.schemas.credentials import (
    AwsDelegationSetupData,
    AwsDelegationSetupResponse,
    CloudAccountListData,
    CloudAccountListResponse,
    CloudAccountResponse,
    CreateCredentialRequest,
    CredentialListData,
    CredentialListResponse,
    CredentialOrderRequest,
    CredentialResponse,
    NetworkResourcesData,
    NetworkResourcesResponse,
    PatchCloudAccountRequest,
    PatchCredentialRequest,
    VerifyResponse,
    VerifyResponseData,
    VmSkuAvailabilityData,
    VmSkuAvailabilityItem,
    VmSkuAvailabilityResponse,
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


# 사용자가 스스로 원인을 좁힐 수 있게 하는 안내 문구. STS는 역할 이름·신뢰 정책의 계정 ID·
# ExternalId 중 무엇이 틀려도 똑같이 AccessDenied를 주기 때문에 셋을 모두 적는다.
DELEGATION_TROUBLESHOOTING = [
    "역할 이름이 허용 접두사로 시작하는지 확인하세요.",
    "역할의 신뢰 정책 Principal이 이 서비스의 AWS 계정 ID인지 확인하세요.",
    "신뢰 정책의 ExternalId가 등록한 값과 같은지 확인하세요.",
]

_VERIFICATION_MESSAGES = {
    "CLOUD_PERMISSION_DENIED": "역할을 빌릴 수 없습니다. " + " ".join(DELEGATION_TROUBLESHOOTING),
    "CREDENTIAL_ACCOUNT_MISMATCH": (
        "역할이 속한 AWS 계정이 등록한 계정 ID와 다릅니다. 다른 계정의 역할이라면 계정을 새로 "
        "등록해 주세요."
    ),
    "PROVIDER_AUTHENTICATION_FAILED": "자격 증명으로 AWS 인증에 실패했습니다.",
    "PROVIDER_API_ERROR": "AWS 호출에 실패했습니다. 잠시 후 다시 시도해 주세요.",
}


def _verification_message(error_code: str | None) -> str | None:
    if not error_code:
        return None
    return _VERIFICATION_MESSAGES.get(error_code, "자격 증명 검증에 실패했습니다.")


def _serialize_credential(credential: Credential, verification: VerificationResult | None = None) -> dict:
    """`verification`은 방금 수행한 검증 결과다 — 실패 사유를 응답에만 실어 보내기 위한 것이고
    DB에 저장하지 않는다(목록 조회에서는 항상 None)."""
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
        # 인증 방식은 별도 컬럼을 만들지 않고 tags(JSONB)에 비밀 아닌 힌트로 넣어 둔다 —
        # 목록 조회에서 payload를 복호화하지 않고도 "레거시 키" 배지를 띄울 수 있어야 한다.
        "auth_type": (credential.tags or {}).get("auth_type") or AUTH_TYPE_ACCESS_KEY,
        "verification_error_code": verification.error_code if verification else None,
        "verification_error_message": _verification_message(verification.error_code) if verification else None,
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


@router.get("/credentials/aws/delegation-setup", response_model=AwsDelegationSetupResponse)
def aws_delegation_setup(
    current_user: User = Depends(get_current_user),
) -> AwsDelegationSetupResponse:
    """AWS 역할 위임 온보딩에 필요한 값을 내려준다.

    사용자는 이 응답을 보고 **자기 AWS 계정에** 역할을 만든다. 우리가 저장하는 건 그 결과로
    받은 `role_arn`과 여기서 발급한 `external_id`뿐이고, 둘 다 비밀이 아니다.

    `external_id`는 요청할 때마다 새로 발급한다(서버에 보관하지 않는다). 사용자가 이 값을
    신뢰 정책에 넣고 등록 요청에 그대로 실어 보내면 되는 구조라, 발급 상태를 들고 있을 필요가
    없다. 등록 요청이 `external_id`를 직접 담아 보내는 것도 허용한다 — 역할을 먼저 만들어 둔
    경우를 위해서다.

    ⚠️ 이 라우트는 `POST /credentials/{provider}`와 경로 모양이 비슷하지만 메서드가 달라
    충돌하지 않는다. 다만 순서상 먼저 선언해 `{provider}`가 "aws"를 삼키지 않게 한다.
    """
    settings = get_settings()
    if not settings.platform_aws_account_id:
        raise ApiError(
            503,
            "PLATFORM_AWS_NOT_CONFIGURED",
            "서비스의 AWS 계정 설정이 없어 역할 위임 연결을 안내할 수 없습니다. 관리자에게 문의해 주세요.",
        )

    external_id = str(uuid.uuid4())
    # 허용 패턴(arn:aws:iam::*:role/MultiCloudOps*)에서 역할 이름 접두사만 뽑아 화면에 보여준다.
    role_name_prefix = settings.platform_aws_assumable_role_pattern.rsplit("/", 1)[-1].rstrip("*")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                # 계정 root를 Principal로 둔다 — IAM 사용자 ARN을 직접 쓰면 그 사용자를 지웠다
                # 다시 만들었을 때 내부 고유 ID가 달라져 고객 쪽 신뢰가 전부 깨진다.
                "Principal": {"AWS": f"arn:aws:iam::{settings.platform_aws_account_id}:root"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"sts:ExternalId": external_id}},
            }
        ],
    }

    return AwsDelegationSetupResponse(
        data=AwsDelegationSetupData(
            platform_account_id=settings.platform_aws_account_id,
            external_id=external_id,
            role_name_prefix=role_name_prefix,
            suggested_role_name=f"{role_name_prefix}Access",
            trust_policy=trust_policy,
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/AmazonEC2FullAccess",
                "arn:aws:iam::aws:policy/AmazonRDSFullAccess",
                "arn:aws:iam::aws:policy/AmazonS3FullAccess",
                "arn:aws:iam::aws:policy/CloudFrontFullAccess",
            ],
            inline_statements=[
                {
                    # 인벤토리 비용 표시와 permission_scope 자동 판별에 쓰는 읽기 전용 권한 +
                    # 프로비저닝 폼 "기존 리소스 사용"이 실제 VPC/서브넷/보안 그룹 목록을 조회하는
                    # 데 쓰는 읽기 전용 권한(2026-09-17 추가 — AmazonEC2FullAccess엔 원래
                    # 포함되지만, 그 managed policy 없이 좁은 인라인 정책만으로 역할을 만든
                    # 사용자는 이 조회가 막혔다 — 실사용 중 발견) + EC2 프로비저닝 자체가
                    # "자동 생성"(기존 리소스 미지정) 경로에서 항상 거치는 조회 2종
                    # (2026-09-17 실제 AWS 계정으로 EC2 생성 테스트 중 `ec2:DescribeVpcAttribute
                    # AccessDenied`로 실패하는 걸 발견해서 추가) — `terraform/aws/ec2/main.tf`의
                    # `data "aws_vpc"`가 vpc_id 지정 여부와 무관하게 항상 enableDnsSupport/
                    # enableDnsHostnames를 읽고(`DescribeVpcAttribute`), `data
                    # "aws_availability_zones"`는 (fallback 서브넷을 실제로 만들지 않아도) 매
                    # apply마다 무조건 평가된다(`DescribeAvailabilityZones`) — 둘 다
                    # `AmazonEC2FullAccess`에 포함되지만 그게 제대로 붙지 않은 역할에서 막히는
                    # 게 실사용으로 확인됐다. `ec2:DescribeSecurityGroupRules`는 보안그룹 관리
                    # 화면(`app/routers/security_groups.py`)이 규칙 목록을 조회하는 데 쓴다 —
                    # `SecurityGroupRuleId` 기반으로 규칙을 정확히 지정해야 삭제할 수 있어서
                    # `describe_security_groups()`만으로는 부족하다.
                    "Effect": "Allow",
                    "Action": [
                        "ce:GetCostAndUsage",
                        "iam:SimulatePrincipalPolicy",
                        "ec2:DescribeVpcs",
                        "ec2:DescribeVpcAttribute",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeSecurityGroups",
                        "ec2:DescribeSecurityGroupRules",
                        "ec2:DescribeAvailabilityZones",
                    ],
                    "Resource": "*",
                },
                {
                    # EC2 프로비저닝(app/aws_provisioning.py)이 SSH 키 대신 SSM Session Manager로
                    # 접속하게 하려고 인스턴스마다 `mcp-ssm-*` IAM 역할/인스턴스 프로파일을 만든다
                    # (terraform/aws/ec2/main.tf). 이 위임 역할 자체가 그 역할을 만들고 인스턴스에
                    # 넘길(`iam:PassRole`) 권한이 없으면 실제 AWS 계정으로 테스트 중
                    # `iam:ListRolePolicies AccessDenied`로 막힌다(2026-09-17 실사용 중 발견 —
                    # terraform이 aws_iam_role을 생성한 직후 state 갱신을 위해 인라인/첨부 정책
                    # 목록까지 읽는다). `iam:PassRole`은 임의 역할에 주면 위임 세션이 그 역할로
                    # 권한을 상승시킬 수 있어(예: 관리자 역할을 결제 리소스에 붙이는 식) 절대
                    # Resource: "*"로 주지 않는다 — 우리가 만드는 `mcp-ssm-*` 역할/프로파일로만
                    # 좁힌다(§2의 `MultiCloudOps*` 역할 이름 제한과 같은 원칙).
                    "Effect": "Allow",
                    "Action": [
                        "iam:CreateRole",
                        "iam:GetRole",
                        "iam:DeleteRole",
                        "iam:ListRolePolicies",
                        "iam:ListAttachedRolePolicies",
                        "iam:ListRoleTags",
                        "iam:TagRole",
                        "iam:UntagRole",
                        "iam:ListInstanceProfilesForRole",
                        "iam:AttachRolePolicy",
                        "iam:DetachRolePolicy",
                        "iam:CreateInstanceProfile",
                        "iam:GetInstanceProfile",
                        "iam:DeleteInstanceProfile",
                        "iam:AddRoleToInstanceProfile",
                        "iam:RemoveRoleFromInstanceProfile",
                        "iam:PassRole",
                    ],
                    "Resource": [
                        "arn:aws:iam::*:role/mcp-ssm-*",
                        "arn:aws:iam::*:instance-profile/mcp-ssm-*",
                    ],
                },
                {
                    # 보안그룹 관리 화면(2026-09-17, `app/routers/security_groups.py`)이 프로비저닝과
                    # 별개로 직접 SG를 생성/삭제하고 규칙을 추가/삭제하는 데 쓰는 권한. 새로 만들
                    # SG의 ID는 생성 시점에야 정해져 `mcp-ssm-*` 같은 이름 접두사 스코핑이 불가능하다
                    # — 하지만 이 권한들은 `iam:PassRole`(권한 상승 위험, 위 statement 참고)과 달리
                    # 자기 계정 안의 네트워크 규칙만 바꿀 수 있어 `Resource: "*"`로 둬도
                    # `AmazonEC2FullAccess`가 이미 부여하는 것과 실질적으로 같은 위험 수준이다.
                    "Effect": "Allow",
                    "Action": [
                        "ec2:CreateSecurityGroup",
                        "ec2:DeleteSecurityGroup",
                        "ec2:AuthorizeSecurityGroupIngress",
                        "ec2:AuthorizeSecurityGroupEgress",
                        "ec2:RevokeSecurityGroupIngress",
                        "ec2:RevokeSecurityGroupEgress",
                        "ec2:CreateTags",
                    ],
                    "Resource": "*",
                },
                {
                    # 인벤토리의 "AWS CLI로 접속" 기능(`issue_cli_session()`,
                    # `POST /resources/{id}/cli-access`)이 실제로 `aws ssm start-session`을 쓰는
                    # 데 필요한 권한(2026-09-17 실사용 중 `ssm:StartSession AccessDenied`로 발견).
                    # `mcp-ssm-*` 역할에 붙인 `AmazonSSMManagedInstanceCore`(§7)는 "인스턴스가
                    # SSM에 등록되는" 권한이고, 이건 그것과 별개로 "사용자가 그 세션을 여는" 권한이라
                    # 위임 역할 쪽에 따로 있어야 한다. `iam:PassRole`과 달리 이미 갖고 있는 EC2 전체
                    # 제어 권한(인스턴스 시작/중지/삭제) 이상으로 위험 범위를 넓히지 않아 `Resource:
                    # "*"`로 둔다.
                    "Effect": "Allow",
                    "Action": [
                        "ssm:StartSession",
                        "ssm:TerminateSession",
                        "ssm:ResumeSession",
                        "ssm:DescribeSessions",
                        "ssm:DescribeInstanceInformation",
                        "ssm:GetConnectionStatus",
                    ],
                    "Resource": "*",
                },
                {
                    # 프로비저닝 기능(EC2/RDS/S3/CloudFront, `app/aws_provisioning.py` 외 3개
                    # 러너 → `terraform/aws/{ec2,rds,s3,cloudfront}`)이 실제로 리소스를 생성·삭제
                    # 할 때 쓰는 액션(2026-09-18 추가). 오늘은 `managed_policy_arns`의 4개
                    # FullAccess 정책(①)이 이 권한을 이미 포함하고 있어 기능상 필수는 아니다 —
                    # 다만 화면에서 "관리형 정책 4개를 붙이세요"라고만 안내하면 실제로 무슨 권한이
                    # 쓰이는지 확인할 방법이 없어서(FullAccess는 서비스 전체 권한이라 이 기능이
                    # 정확히 어디까지 쓰는지 알 수 없음), 최소 권한을 원하는 회사가 ①의 4개
                    # FullAccess 정책 대신 이 statement만으로도 프로비저닝을 시도해 볼 수 있도록
                    # `docs/AWS_Azure_GCP_Permission_Reference_2026-09-17.md`의 "관리형 정책에서
                    # 커버되는 프로비저닝 액션" 표를 그대로 옮겨 명시했다. ⚠️ 이 목록은 실제 코드가
                    # 호출하는 terraform 리소스 블록 기준으로 뽑은 것이라 Terraform AWS provider가
                    # 내부적으로 거치는 모든 조회(예: EC2에서 실제로 겪었던 `DescribeVpcAttribute`
                    # 같은 숨은 호출)까지 전부 검증된 것은 아니다 — 최소 권한으로 좁혀 쓰려는
                    # 경우 ①의 관리형 정책을 당장 떼지 말고 먼저 이 statement와 함께 테스트해 보길
                    # 권장한다. `ec2:Describe{Vpcs,VpcAttribute,Subnets,SecurityGroups,
                    # SecurityGroupRules,AvailabilityZones}`/`ec2:{Create,Authorize,Revoke}
                    # SecurityGroup*`/`ec2:CreateTags`는 위 ①/③에 이미 있어 중복 나열하지 않는다.
                    "Effect": "Allow",
                    "Action": [
                        "ec2:RunInstances",
                        "ec2:TerminateInstances",
                        "ec2:DescribeInstances",
                        "ec2:DescribeImages",
                        "ec2:CreateSubnet",
                        "rds:CreateDBInstance",
                        "rds:CreateDBSubnetGroup",
                        "rds:AddTagsToResource",
                        "rds:DeleteDBInstance",
                        "rds:DescribeDBInstances",
                        "rds:DescribeDBSubnetGroups",
                        "s3:CreateBucket",
                        "s3:DeleteBucket",
                        "s3:PutBucketPublicAccessBlock",
                        "s3:GetBucketPublicAccessBlock",
                        "s3:PutBucketVersioning",
                        "s3:GetBucketVersioning",
                        "s3:GetBucketLocation",
                        "cloudfront:CreateDistribution",
                        "cloudfront:GetDistribution",
                        "cloudfront:UpdateDistribution",
                        "cloudfront:DeleteDistribution",
                        "cloudfront:TagResource",
                    ],
                    "Resource": "*",
                },
            ],
            iam_console_url="https://console.aws.amazon.com/iam/home#/roles/create",
            troubleshooting=DELEGATION_TROUBLESHOOTING,
        )
    )


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

    # 인증 방식 힌트를 tags에 남긴다(비밀 아님). 사용자가 같은 키로 tags를 보내도 서버 값이
    # 우선한다 — 화면 배지의 근거라 사용자 입력으로 뒤집히면 안 된다.
    auth_type = auth_type_of(payload.secret_payload)
    tags = {**payload.tags, "auth_type": auth_type}

    ciphertext, nonce = encrypt_credential_json(payload.secret_payload)
    credential = Credential(
        cloud_account_id=account.id,
        name=payload.name,
        encrypted_payload=ciphertext,
        encryption_nonce=nonce,
        encryption_key_version=get_settings().credential_encryption_key_version,
        public_identifier=mask_public_identifier(payload.public_identifier) if payload.public_identifier else None,
        tags=tags,
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
    return CredentialResponse(data=_serialize_credential(credential, verification))


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
    verification: VerificationResult | None = None

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
        # 키 교체로 인증 방식이 바뀔 수 있다(레거시 키 → 역할 위임). tags의 힌트도 같이 갱신해야
        # 화면 배지가 실제 payload와 어긋나지 않는다.
        credential.tags = {**(credential.tags or {}), "auth_type": auth_type_of(payload.secret_payload)}
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
    return CredentialResponse(data=_serialize_credential(credential, verification))


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
            verification_error_code=verification.error_code,
            verification_error_message=_verification_message(verification.error_code),
        )
    )


@router.get("/credentials/{credential_id}/network-resources", response_model=NetworkResourcesResponse)
def get_network_resources(
    credential_id: str,
    region: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NetworkResourcesResponse:
    """프로비저닝 폼 "기존 리소스 사용"에서 실제 VPC/서브넷/보안 그룹(Azure는 리소스 그룹/VNet/
    NSG, GCP는 VPC 네트워크) 목록을 조회한다(2026-09-17 — 전엔 사용자가 직접 콘솔에서 ID를
    찾아 빈칸에 타이핑해야 했다). AWS는 리전별 리소스라 `region` 쿼리 파라미터가 필수, Azure/GCP는
    구독/프로젝트 전체를 조회하므로 필요 없다."""
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

    # 위임 credential이면 임시 자격증명을 발급받는다(레거시는 그대로 통과) — resources.py의
    # resource_control 액션과 동일 패턴.
    try:
        secret_payload = resolve_secret_payload(account.provider, secret_payload, credential_id=credential.id)
    except CredentialResolutionError as exc:
        raise ApiError(422, exc.error_code, exc.message or "임시 자격 증명을 발급받지 못했습니다.")

    try:
        if account.provider == "aws":
            data = aws_provider.list_network_resources(secret_payload, region)
        elif account.provider == "azure":
            data = azure_provider.list_network_resources(secret_payload, account.external_account_id)
        elif account.provider == "gcp":
            data = gcp_provider.list_network_resources(secret_payload, account.external_account_id)
        else:
            data = {}
    except ResourceActionError as exc:
        raise ApiError(502, exc.code, "클라우드에서 네트워크 리소스 목록을 가져오지 못했습니다.") from exc
    finally:
        del secret_payload

    return NetworkResourcesResponse(data=NetworkResourcesData(**data))


@router.get("/credentials/{credential_id}/vm-sku-availability", response_model=VmSkuAvailabilityResponse)
def get_vm_sku_availability(
    credential_id: str,
    region: str = Query(...),
    sku: list[str] = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VmSkuAvailabilityResponse:
    """Azure VM SKU 사전 확인(2026-09-18, 읽기 전용) — 프로비저닝 폼에서 "경량" 등급 기본 SKU
    (B1s)나 무료 대안(B2ats_v2)이 실제 이 구독·리전에서 생성 가능한지 실제 생성 전에 미리 걸러
    본다. Azure 무료 체험 계정이 안내하는 "무료 혜택 대상"과 "이 구독·리전에서 지금 만들 수
    있음"은 별개 질문이다(SkuNotAvailable/Capacity Restrictions 실측 — CLAUDE.md·설계 문서
    참고). 시크릿은 절대 응답에 포함하지 않는다.

    이 조회는 스냅샷일 뿐 실시간 용량(capacity)까지 보장하지 않는다 — "available"이었어도
    실제 생성 시 Azure가 다시 검증하며 거부될 수 있다. Azure 외 provider는 지원하지 않는다."""
    credential, account = _get_owned_credential(db, current_user.id, credential_id)

    if account.provider != "azure":
        raise ApiError(422, "UNSUPPORTED_OPERATION", "VM SKU 사전 확인은 Azure만 지원합니다.")

    if not credential.verified:
        raise ApiError(422, "CLOUD_PERMISSION_DENIED", "검증된 자격 증명이 아닙니다 — 먼저 재검증하세요.")

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        raise ApiError(422, "PROVIDER_API_ERROR", "자격 증명을 복호화하지 못했습니다.")

    # 위임 credential이면 임시 자격증명을 발급받는다(network-resources와 동일 패턴).
    try:
        secret_payload = resolve_secret_payload(account.provider, secret_payload, credential_id=credential.id)
    except CredentialResolutionError as exc:
        raise ApiError(422, exc.error_code, exc.message or "임시 자격 증명을 발급받지 못했습니다.")

    try:
        result = azure_provider.list_vm_sku_availability(secret_payload, account.external_account_id, region, sku)
    except ResourceActionError as exc:
        raise ApiError(502, exc.code, "클라우드에서 VM SKU 가용성을 조회하지 못했습니다.") from exc
    finally:
        del secret_payload

    return VmSkuAvailabilityResponse(
        data=VmSkuAvailabilityData(
            region=region,
            skus=[
                VmSkuAvailabilityItem(sku=name, status=v["status"], reason=v["reason"])
                for name, v in result.items()
            ],
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
