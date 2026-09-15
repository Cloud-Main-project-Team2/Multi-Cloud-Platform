"""저장된 credential payload → 실제로 CSP를 호출할 때 쓸 자격증명으로 변환.

이 모듈이 있는 이유는 **인증 방식 전환을 "교체"가 아니라 "추가"로 만들기 위해서**다.
`decrypt_credential_json()` 호출부 5곳(`routers/{credentials,provisioning,resources,sync_jobs}.py`,
`dev_destroy_job.py`)이 전부 "복호화 → dict → provider 어댑터/러너"라는 같은 모양이라,
그 사이에 이 함수 하나만 끼우면 하류 코드는 시그니처도 동작도 그대로 유지된다.

- **레거시(access_key)**: payload를 그대로 통과시킨다. 기존에 등록된 AWS/Azure/GCP credential과
  `seed_mock_data.py`의 목업이 전부 이 경로이고, 코드 경로가 하나도 바뀌지 않는다.
- **AWS 위임(assume_role)**: 우리 플랫폼 신원으로 `sts:AssumeRole`을 호출해 1시간짜리 임시
  자격증명 3종(`access_key_id`/`secret_access_key`/`session_token`)을 받아, 레거시와 **같은 키
  이름**의 dict로 돌려준다. 그래서 `providers/aws.py:_client()`와 AWS 러너 4개의
  `_credential_env()`가 이미 갖고 있던 `session_token` 지원을 그대로 탄다.

임시 자격증명은 **메모리에만** 존재한다 — DB에 저장하지 않고, 로그·감사 이벤트·에러 메시지에도
남기지 않는다(§18 "복호화 범위 최소화"와 같은 원칙).
"""

from __future__ import annotations

import datetime as dt
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings
from app.providers import AUTH_TYPE_ASSUME_ROLE, auth_type_of

logger = logging.getLogger(__name__)

_TIMEOUT_CONFIG = Config(connect_timeout=5, read_timeout=8, retries={"max_attempts": 1})

# STS 세션 이름은 고객 계정의 CloudTrail에 그대로 찍힌다 — 누가 왜 들어왔는지 고객이 알아볼 수
# 있어야 하므로 서비스 이름 + credential id로 구성한다. 허용 문자는 [\w+=,.@-]{2,64}.
_SESSION_NAME_PREFIX = "multicloud-ops"


class CredentialResolutionError(Exception):
    """임시 자격증명을 발급하지 못했다. `error_code`는 API 에러 코드로 그대로 쓴다."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _session_name(credential_id: int | None) -> str:
    return f"{_SESSION_NAME_PREFIX}-{credential_id}" if credential_id else _SESSION_NAME_PREFIX


def _platform_sts_client():
    """AssumeRole을 호출할 '출발 신원'으로 STS 클라이언트를 만든다.

    설정에 키가 있으면 그것으로, 없으면 boto3 기본 자격증명 체인(EC2 instance role,
    ECS task role, 환경변수 등)에 맡긴다 — 서버를 AWS 위에 올리면 키 설정을 비우기만 하면
    장기 키가 완전히 사라진다.
    """
    settings = get_settings()
    if settings.platform_aws_access_key_id and settings.platform_aws_secret_access_key:
        return boto3.client(
            "sts",
            aws_access_key_id=settings.platform_aws_access_key_id,
            aws_secret_access_key=settings.platform_aws_secret_access_key,
            region_name="us-east-1",
            config=_TIMEOUT_CONFIG,
        )
    return boto3.client("sts", region_name="us-east-1", config=_TIMEOUT_CONFIG)


def assume_role(
    role_arn: str,
    external_id: str,
    *,
    credential_id: int | None = None,
    duration_seconds: int | None = None,
) -> dict:
    """역할을 빌려 임시 자격증명 3종을 받는다.

    반환 dict의 키 이름은 레거시 payload와 같다(`access_key_id`/`secret_access_key`) — 하류가
    두 방식을 구분할 필요가 없게 하기 위한 의도적 선택이며, 여기에 `session_token`과
    `expiration`이 추가된다.

    `duration_seconds`를 주면 기본 세션 수명 대신 그 값을 쓴다 — 사용자에게 직접 건네는
    자격증명(CLI 접속 등)은 더 짧게 끊기 위한 것이다. 역할의 MaxSessionDuration을 넘으면
    STS가 거부한다.
    """
    settings = get_settings()
    try:
        response = _platform_sts_client().assume_role(
            RoleArn=role_arn,
            RoleSessionName=_session_name(credential_id),
            ExternalId=external_id,
            DurationSeconds=duration_seconds or settings.platform_aws_session_duration_seconds,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        # AccessDenied는 원인이 세 가지(역할 이름/신뢰 정책의 계정 ID/ExternalId)인데 STS가
        # 구분해주지 않는다. 사용자가 스스로 좁힐 수 있도록 메시지에 점검 항목을 적어둔다.
        if code in ("AccessDenied", "AccessDeniedException"):
            raise CredentialResolutionError(
                "CLOUD_PERMISSION_DENIED",
                "역할을 빌릴 수 없습니다. ①역할 이름이 허용 패턴"
                f"({settings.platform_aws_assumable_role_pattern})에 맞는지 ②신뢰 정책의 계정 ID가 "
                f"{settings.platform_aws_account_id or '우리 플랫폼 계정'}인지 "
                "③ExternalId가 일치하는지 확인해 주세요.",
            ) from exc
        if code in ("ExpiredToken", "InvalidClientTokenId", "SignatureDoesNotMatch", "UnrecognizedClientException"):
            # 사용자 잘못이 아니라 우리 플랫폼 자격증명 문제다.
            logger.error("platform_credentials_invalid", extra={"sts_error_code": code})
            raise CredentialResolutionError(
                "PROVIDER_AUTHENTICATION_FAILED",
                "서비스의 AWS 자격 증명 설정에 문제가 있습니다. 관리자에게 문의해 주세요.",
            ) from exc
        raise CredentialResolutionError(
            "PROVIDER_API_ERROR", "AWS 임시 자격 증명 발급에 실패했습니다."
        ) from exc
    except BotoCoreError as exc:
        raise CredentialResolutionError(
            "PROVIDER_API_ERROR", "AWS 임시 자격 증명 발급에 실패했습니다."
        ) from exc

    creds = response["Credentials"]
    expiration = creds.get("Expiration")
    return {
        "access_key_id": creds["AccessKeyId"],
        "secret_access_key": creds["SecretAccessKey"],
        "session_token": creds["SessionToken"],
        "expiration": expiration.isoformat() if isinstance(expiration, dt.datetime) else None,
    }


def resolve_secret_payload(
    provider: str, stored_payload: dict, *, credential_id: int | None = None
) -> dict:
    """저장된 payload를 '지금 바로 쓸 수 있는' 자격증명 dict로 바꾼다.

    레거시 payload는 그대로 통과한다 — 이 함수를 끼워도 기존 credential의 동작은 바뀌지 않는다.
    """
    if provider != "aws" or auth_type_of(stored_payload) != AUTH_TYPE_ASSUME_ROLE:
        return stored_payload

    role_arn = stored_payload.get("role_arn")
    external_id = stored_payload.get("external_id")
    if not role_arn or not external_id:
        raise CredentialResolutionError(
            "CREDENTIAL_VERIFICATION_FAILED", "위임 자격 증명에 role_arn/external_id가 없습니다."
        )
    return assume_role(role_arn, external_id, credential_id=credential_id)
