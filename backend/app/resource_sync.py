"""리소스 동기화 — 실제 CSP 목록 조회 (API 명세서 v1.1 §9).

`app/providers/{aws,azure,gcp}.py`의 `discover_resources()`가 실제 SDK로 목록을 가져오고,
이 모듈은 그 결과를 provider 무관 형태(`DiscoveredResource`)로 표준화해 라우터에 넘긴다.
실행/삭제 어댑터(`app/resource_actions.py`)와 지원 범위를 맞춘다(2026-09-10 결정, CLAUDE.md):
AWS EC2·RDS·S3, Azure VM, GCP Compute Engine만 실제로 동기화하고 나머지 서비스는
`discover_resources()`가 그냥 빈 목록을 반환한다(에러가 아니다 — 아직 지원하지 않을 뿐).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiscoveredResource:
    service_code: str
    external_resource_id: str
    original_resource_type: str
    name: str | None
    region: str | None
    status: str | None
    tags: dict = field(default_factory=dict)


class SyncError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(code)


def discover_resources(provider: str, secret_payload: dict, external_account_id: str) -> list[DiscoveredResource]:
    from app.providers import aws as aws_provider
    from app.providers import azure as azure_provider
    from app.providers import gcp as gcp_provider

    try:
        if provider == "aws":
            return aws_provider.discover_resources(secret_payload)
        if provider == "azure":
            return azure_provider.discover_resources(secret_payload, external_account_id)
        if provider == "gcp":
            return gcp_provider.discover_resources(secret_payload, external_account_id)
        return []
    except SyncError:
        raise
    except Exception as exc:  # noqa: BLE001 — provider adapter는 원문 SDK 예외를 밖으로 흘리지 않는다(§18).
        raise SyncError("PROVIDER_API_ERROR") from exc
