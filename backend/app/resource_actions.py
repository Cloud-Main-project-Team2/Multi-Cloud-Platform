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

나머지(Azure SQL Database/Storage Account/CDN, GCP Cloud SQL/Storage/CDN, AWS CloudFront)는
`UNSUPPORTED_OPERATION`으로 명시적으로 막는다 — 어댑터가 없어서가 아니라 이번 세션 범위 밖으로
의도적으로 뺀 것이다.
"""

from __future__ import annotations


class ResourceActionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def supported_actions(provider: str, service_code: str, original_resource_type: str) -> set[str]:
    if provider == "aws" and service_code == "ec2":
        if original_resource_type == "EBS Volume":
            return {"delete"}
        return {"start", "stop", "delete"}
    if provider == "aws" and service_code == "rds":
        return {"start", "stop", "delete"}
    if provider == "aws" and service_code == "s3":
        return {"delete"}
    if provider == "azure" and service_code == "vm":
        return {"start", "stop", "delete"}
    if provider == "gcp" and service_code == "compute_engine":
        return {"start", "stop", "delete"}
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
                service_code, action, secret_payload, external_account_id, region, external_resource_id
            )
        else:
            raise ResourceActionError("UNSUPPORTED_OPERATION")
    except ResourceActionError:
        raise
    except Exception as exc:  # noqa: BLE001 — provider adapter는 원문 SDK 예외를 밖으로 흘리지 않는다(§18).
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
