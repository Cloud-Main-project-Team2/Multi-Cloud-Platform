"""리소스 시작·중지·삭제 실행 (API 명세서 v1.1 §8.5).

Terraform과 무관하게 서비스별 SDK 어댑터(`app/providers/{aws,azure,gcp}.py`)가 직접 담당한다.
`resource_type_id` 매핑이나 `supports_*` 플래그 조회 방식이 아니라, 이 모듈의
`supported_actions()`가 (provider, service_code, original_resource_type) 조합을 보고
서버 코드로 직접 판단한다.

**지원 범위 결정(2026-09-10, `solcho/be-resources-api`, CLAUDE.md 기록)**: 이 저장소에는
`aws_client.py`/`azure_client.py`/`gcp_client.py` 어댑터가 아직 없었다(구버전 프로토타입 세션이
실행된 적 없음). §0의 "최소 EC2/RDS/S3" 지시에 따라 다음만 실제로 구현했다.

- AWS: EC2 인스턴스(start/stop/delete), EBS Volume(delete만), RDS 인스턴스(start/stop/delete),
  S3 버킷(delete만, 비어있지 않으면 `BucketNotEmpty` → `force_empty=true` 재요청 지원)
- Azure: Virtual Machine(start/stop/delete) — 목업 리소스(mcp-c3d4-vm)가 이 경로를 타므로 추가
- GCP: Compute Engine 인스턴스(start/stop/delete) — 같은 이유로 추가

나머지(Azure SQL Database/Storage Account/CDN, AWS CloudFront)는 당시 `UNSUPPORTED_OPERATION`으로
명시적으로 막았다 — 어댑터가 없어서가 아니라 그 세션 범위 밖으로 의도적으로 뺀 것이었다(전부
아래에서 후속 세션에 채워졌다).

**GCP Cloud SQL/Storage/CDN 지원 추가(2026-09-17, `gwonhyung/be-gcp-resource-actions`)**:
AWS는 RDS(start/stop/delete)·S3(delete)까지 지원하는데 GCP는 Compute Engine만 지원해 3사 간
기능 격차가 있었다(실사용자 확인). AWS와 동일한 서비스 등급 기준으로 맞춘다.

- Cloud SQL 인스턴스: start/stop/delete — RDS와 동일하게 셋 다 지원. Cloud SQL Admin API는
  전용 start/stop 엔드포인트가 없어 `settings.activationPolicy`를 `ALWAYS`(start)/`NEVER`(stop)로
  PATCH하는 방식으로 구현한다(GCP 공식 문서 권장 패턴).
- Cloud Storage 버킷: delete만 — S3와 동일한 이유(버킷 자체엔 시작/중지 개념이 없음). 비어있지
  않으면 `force_empty=true`로 먼저 객체를 지우고 재시도하는 것도 S3와 동일하게 지원한다.
- Cloud CDN(전역 forwarding rule): delete만 — 로드밸런서 리소스에도 시작/중지 개념이 없다.

세 서비스 모두 RDS/EC2처럼 **호출이 accept되면 성공으로 본다**(작업 완료까지 폴링하지 않음) —
"CSP 호출은 완료를 기다리지 않을 수 있다"는 기존 결정과 같은 비대칭 정책을 그대로 따른다.

**AWS CloudFront 삭제 지원 추가(2026-09-18)**: 3사 프로비저닝 가능 서비스 중 유일하게 어떤
동작도 안 되던 서비스였다(사용자 확인 — "프로비저닝으로 만든 건 다 start/stop/delete가 돼야
한다"). delete만 지원한다(배포 자체엔 시작/중지 개념이 없음, S3/GCS/CDN류와 동일). CloudFront는
활성화된 배포를 바로 못 지우게 막는다(`DistributionNotDisabled`) — 지우기 전에 먼저 비활성화를
요청하고, 그 반영에 보통 수 분~수십 분이 걸려 같은 요청 안에서 끝낼 수 없으므로(S3의
`force_empty`처럼 즉시 재시도가 안 됨) `CloudFrontNotDisabled` 에러로 안내만 하고 끝낸다 —
사용자가 잠시 후 삭제를 다시 시도하면 된다.
"""

from __future__ import annotations


class ResourceActionError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        # CSP SDK 예외의 원문(사용자에게 보여줄 구체 원인의 재료). secret은 제거된 상태로 담는다.
        # code만 있는 사전 검사 실패(UNSUPPORTED_OPERATION 등)는 None.
        self.message = message
        super().__init__(code)


_SDK_MESSAGE_MAX = 1000


def _secret_values(payload) -> list[str]:
    """secret_payload에 담긴 모든 문자열 값을 평탄화한다(원문 redact용)."""
    values: list[str] = []

    def _walk(value) -> None:
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                _walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                _walk(item)

    _walk(payload)
    return values


def _redact_sdk_message(message: str, secret_payload: dict) -> str:
    """SDK 예외 원문에서 secret을 제거하고 길이를 제한한다."""
    from app.terraform_runner import redact

    return redact(message, _secret_values(secret_payload)).strip()[:_SDK_MESSAGE_MAX]


def fill_message_from_cause(err: ResourceActionError, secret_payload: dict) -> None:
    """provider 어댑터가 `raise ResourceActionError(code) from exc`로 감싸며 message를 안 채운
    경우, `__cause__`에 남은 원문 SDK 예외를 secret 제거 후 채운다. `perform_action()`뿐 아니라
    `app/routers/security_groups.py`처럼 provider 함수를 직접 호출하는 경로도 같은 방식으로
    "구체 원인"(§14, `app.error_patterns.translate_reason`)을 보여줄 수 있어야 해서 공용
    함수로 뺐다(2026-09-17)."""
    if err.message is None and err.__cause__ is not None:
        err.message = _redact_sdk_message(str(err.__cause__), secret_payload)


def supported_actions(provider: str, service_code: str, original_resource_type: str) -> set[str]:
    if provider == "aws" and service_code == "ec2":
        if original_resource_type == "EBS Volume":
            return {"delete"}
        return {"start", "stop", "delete"}
    if provider == "aws" and service_code == "rds":
        return {"start", "stop", "delete"}
    if provider == "aws" and service_code == "s3":
        return {"delete"}
    if provider == "aws" and service_code == "cloudfront":
        return {"delete"}
    if provider == "azure" and service_code == "vm":
        return {"start", "stop", "delete"}
    if provider == "gcp" and service_code == "compute_engine":
        return {"start", "stop", "delete"}
    if provider == "gcp" and service_code == "cloud_sql":
        return {"start", "stop", "delete"}
    if provider == "gcp" and service_code in ("cloud_storage", "cloud_cdn"):
        return {"delete"}
    return set()


def perform_action(
    *,
    provider: str,
    service_code: str,
    original_resource_type: str,
    action: str,
    secret_payload: dict,
    external_account_id: str,
    region: str | None,
    external_resource_id: str,
    force_empty: bool = False,
) -> None:
    """서비스별 SDK 어댑터로 실제 동작을 수행한다. 실패 시 ResourceActionError를 raise한다."""
    from app.providers import aws as aws_provider
    from app.providers import azure as azure_provider
    from app.providers import gcp as gcp_provider

    if action not in supported_actions(provider, service_code, original_resource_type):
        raise ResourceActionError("UNSUPPORTED_OPERATION")

    try:
        if provider == "aws":
            aws_provider.perform_resource_action(
                service_code, original_resource_type, action, secret_payload, region, external_resource_id,
                force_empty=force_empty,
            )
        elif provider == "azure":
            azure_provider.perform_resource_action(service_code, action, secret_payload, external_resource_id)
        elif provider == "gcp":
            gcp_provider.perform_resource_action(
                service_code, action, secret_payload, external_account_id, region, external_resource_id,
                force_empty=force_empty,
            )
        else:
            raise ResourceActionError("UNSUPPORTED_OPERATION")
    except ResourceActionError as err:
        # provider 어댑터가 `raise ResourceActionError(code) from exc`로 감쌀 때, 원문 SDK 예외는
        # __cause__에 남아 있다. 코드만으로는 알 수 없는 구체 원인을 여기서 한 번에 채운다(§18 —
        # 원문을 그대로 흘리지 않고 secret을 제거해 담는다).
        fill_message_from_cause(err, secret_payload)
        raise
    except Exception as exc:  # noqa: BLE001 — provider adapter는 원문 SDK 예외를 밖으로 흘리지 않는다(§18).
        raise ResourceActionError(
            "PROVIDER_API_ERROR", _redact_sdk_message(str(exc), secret_payload)
        ) from exc
