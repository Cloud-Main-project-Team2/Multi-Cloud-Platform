"""Azure CDN(Front Door Standard) 프로비저닝 — `app/terraform_runner.py` 호출부 (§10.3).

`app/azure_storage_provisioning.py`(Storage Account)·`app/azure_database_provisioning.py`(DB)·
`app/aws_cloudfront_provisioning.py`·`app/gcp_cdn_provisioning.py`와 같은 flat 계약(통합 결정,
2026-09-11)을 따르는 독립 러너다: `validate_spec()`(동기, 요청 처리 중 raise) +
`build_tfvars()` + `run(...)→TerraformResult`(비동기 백그라운드, raise하지 않음) +
`SENSITIVE_PROVIDER_SPEC_FIELDS`. `app/provisioning.py` 레지스트리가 `(azure, cdn)`에 연결한다.

## Classic CDN이 아니라 Front Door인 이유(2026-09-15 결정)

2025-08-15부터 Azure CDN Standard from Microsoft (classic) 신규 프로필 생성·신규 도메인
온보딩·신규 관리형 인증서가 전부 중단됐고(기존 리소스 수정만 2027-09-30까지 지원, Microsoft
Learn classic-cdn-retirement-faq), azurerm provider의 classic SKU 5종
(Akamai/Verizon/Microsoft/ChinaCdn 계열)도 전부 폐지됐다 — **지금 시점엔 Front Door 말고 만들
수 있는 선택지 자체가 없다.** 프론트(`frontend/assets/js/provisioning.js`)의 `CDN_OPTS.azSku`가
원래 `["Standard", "Premium"]`(벤더명 없음)이었던 것과 `healthProbeIntervalSec`(초 단위 간격)
필드도 Front Door 기준으로 설계돼 있었다 — Classic SKU는 항상 `Standard_Microsoft`처럼 벤더명이
붙고, 간격 필드 자체가 없다(둘 다 실제 azurerm provider 스키마 조회로 확인함).

## SKU는 Standard만 허용한다 — 비용

Front Door는 무료 티어가 없고 월 기본료가 Standard $35 / Premium $330(Microsoft Learn 가격
비교, "Compare pricing between Azure Front Door tiers")로 10배 가까이 차이 난다. Premium의 WAF
관리형 규칙·Private Link 오리진은 이 프로젝트가 쓰지 않는 기능이라 이점 없이 비용만 커진다 —
`validate_spec()`이 `Standard` 외 값을 422로 거부하고, 프론트 SKU 드롭다운도 `Standard` 하나만
남겼다.

## Resource Group — 다른 3개 Azure 러너와 달리 프론트 입력값을 그대로 쓴다

`azure_provisioning.py`(VM)/`azure_storage_provisioning.py`/`azure_database_provisioning.py`는
전부 리소스 그룹을 서버가 `rg-{workspace_name}`으로 자동 생성하고 사용자 입력을 안 받는다. 이
러너도 비어 있으면 같은 방식(`rg-cdn-{workspace_name}`)을 쓰지만, `provisioning.js`가 이미 이
필드를 필수 텍스트 입력으로 받고 있어(2026-09-10~11 설계) 값이 오면 버리지 않고 그대로 새
리소스 그룹 이름으로 쓴다.

**사전 존재 확인은 하지 않는다** — `validate_spec()`은 다른 3개 러너와 마찬가지로 credential에
전혀 접근할 수 없는 순수 동기 함수다(`app/routers/provisioning.py`가 credential을 복호화하는
시점은 `run()` 진입 직전뿐이다 — 이 프로젝트의 flat 계약 공통 제약이라 이 러너만 예외로 바꾸지
않는다). 대신 `terraform/azure/cdn/main.tf`의 `azurerm_resource_group` 리소스는 이미 존재하는
이름을 몰래 import해서 관리하지 않고 apply 자체가 실패한다 — 그래서 이미 있는 리소스 그룹을
잘못 관리하다 나중에 destroy로 지워버리는 사고는 이 방식으로도 구조적으로 일어나지 않는다(같은
이름을 다시 쓰면 job이 실패로 끝날 뿐이다).

## 뷰어 프로토콜과 오리진 전달 프로토콜은 다른 축이다

프론트의 `supportedProtocols`(HTTPS만/HTTP+HTTPS)는 뷰어(브라우저)가 Front Door에 접속하는
프로토콜(`route.supported_protocols`)을 뜻한다. `https_redirect_enabled=true`면 azurerm이
`supported_protocols`에 `Http`와 `Https`를 모두 요구한다(그래야 http로 들어온 요청을
리다이렉트할 수 있다) — 그래서 리다이렉트가 켜져 있으면 사용자의 "HTTPS만" 선택과 무관하게 이
목록을 강제로 `["Http", "Https"]`로 만든다(`build_tfvars()`). 대신 오리진으로 실제 전달하는
프로토콜(`route.forwarding_protocol`)에 사용자의 원래 선택을 반영한다 — "HTTPS만"이면
`HttpsOnly`, "HTTP+HTTPS"면 들어온 프로토콜 그대로 전달하는 `MatchRequest`.

## Health Probe/Compression/쿼리스트링 캐시는 accept-if-present

AWS CloudFront(`aws_cloudfront_provisioning.py`)처럼 오리진 하나로 축약하지 않는다 — Front
Door는 `load_balancing`/`patterns_to_match`/`certificate_name_check_enabled` 등 축약 불가능한
Required 필드가 이미 여러 개라(실제 azurerm provider 스키마로 확인함) "오리진 하나만 받고 나머진
하드코딩"하는 최소주의가 성립하지 않는다. 프론트가 이미 받고 있는 값(healthProbeIntervalSec/
Path, compression, queryStringCaching)은 버리지 않고 그대로 반영한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import ApiError, validation_error
from app.terraform_runner import TerraformResult, run_apply

MODULE_DIR = Path(__file__).resolve().parent.parent / "terraform" / "azure" / "cdn"

# CDN 자체에는 만들어질 리소스의 비밀값이 없다(다른 CDN 러너와 동일 원칙).
SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str] = frozenset()

# 공용 buildCommonSpec()이 cdn 종류에 대해 항상 "cdn-xxxxxx" 형태로 자동 생성한다(provisioning.js)
# — aws_cloudfront_provisioning.py와 동일한 이름 규칙을 그대로 쓴다.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$")
# 스킴/경로 없는 호스트명만 허용 — aws_cloudfront_provisioning.py의 _DOMAIN_RE와 동일 규칙.
_ORIGIN_RE = re.compile(r"^(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.(?!-)[a-zA-Z0-9-]{1,63}(?<!-))+$")
# Azure 리소스 그룹 이름 규칙(ARM 공식 제약): 1~90자, 영숫자·마침표·밑줄·하이픈·괄호, 마지막
# 글자는 마침표 불가.
_RG_RE = re.compile(r"^[a-zA-Z0-9._\-()]{1,89}[a-zA-Z0-9_\-()]$")

# Front Door route.cache.query_string_caching_behavior 실제 API 값(azurerm provider 문서).
_QUERY_STRING_CODES = (
    "IgnoreQueryString",
    "UseQueryString",
    "IgnoreSpecifiedQueryStrings",
    "IncludeSpecifiedQueryStrings",
)
_PROTOCOL_CODES = ("https_only", "http_and_https")

_CONTENT_TYPES_TO_COMPRESS = [
    "text/html",
    "text/css",
    "text/plain",
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/xml",
    "image/svg+xml",
    "font/otf",
    "font/ttf",
]


class _CommonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tags: dict[str, str] = Field(default_factory=dict)


class _ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str
    resource_group: str | None = None
    # true면 resource_group을 "새로 만들 이름"이 아니라 "조회할 기존 리소스 그룹 이름"으로 쓴다
    # (2026-09-17 결정 — 그전엔 값이 와도 항상 새로 만들려고 시도해서 기존 이름과 겹치면 그냥
    # apply가 실패했다). false(기본값)면 지금까지 동작 그대로.
    use_existing_resource_group: bool = False
    sku: Literal["Standard"]
    query_string_caching_behavior: str = "IgnoreQueryString"
    protocol: str = "http_and_https"
    health_probe_path: str = "/"
    health_probe_interval_seconds: int = 240
    compression: bool = True
    https_redirect: bool = True


def _derive(common_spec: dict, provider_spec: dict) -> tuple[_CommonSpec, _ProviderSpec]:
    """검증된 `(common, provider)` 모델을 반환한다. 실패 시 422 `ApiError`를 raise한다."""
    try:
        common = _CommonSpec.model_validate(common_spec)
        provider = _ProviderSpec.model_validate(provider_spec)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())) or "provider_spec"
        raise validation_error(
            f"Azure CDN(Front Door) spec 검증 실패: {first.get('msg', 'invalid')}",
            details=[{"field": field, "reason": "invalid"}],
        ) from exc

    if not _NAME_RE.fullmatch(common.name):
        raise validation_error(
            "common_spec.name은 소문자로 시작하는 영소문자/숫자/하이픈 2~40자여야 합니다.",
            details=[{"field": "common_spec.name", "reason": "invalid"}],
        )
    if not _ORIGIN_RE.fullmatch(provider.origin):
        raise validation_error(
            "provider_spec.origin은 스킴/경로 없는 호스트명이어야 합니다(예: example.com).",
            details=[{"field": "provider_spec.origin", "reason": "invalid"}],
        )
    if provider.resource_group is not None and not _RG_RE.fullmatch(provider.resource_group):
        raise validation_error(
            "provider_spec.resource_group은 1~90자의 영숫자/마침표/밑줄/하이픈/괄호여야 하고 "
            "마침표로 끝날 수 없습니다.",
            details=[{"field": "provider_spec.resource_group", "reason": "invalid"}],
        )
    if provider.use_existing_resource_group and provider.resource_group is None:
        raise validation_error(
            "provider_spec.use_existing_resource_group을 쓰려면 provider_spec.resource_group(조회할 "
            "기존 이름)도 함께 지정해야 합니다.",
            details=[{"field": "provider_spec.resource_group", "reason": "required_with_use_existing"}],
        )
    if provider.query_string_caching_behavior not in _QUERY_STRING_CODES:
        raise validation_error(
            f"provider_spec.query_string_caching_behavior은 {', '.join(_QUERY_STRING_CODES)} 중 "
            "하나여야 합니다.",
            details=[{"field": "provider_spec.query_string_caching_behavior", "reason": "invalid"}],
        )
    if provider.protocol not in _PROTOCOL_CODES:
        raise validation_error(
            f"provider_spec.protocol은 {', '.join(_PROTOCOL_CODES)} 중 하나여야 합니다.",
            details=[{"field": "provider_spec.protocol", "reason": "invalid"}],
        )

    return common, provider


def validate_spec(common_spec: dict, provider_spec: dict) -> None:
    """라우터가 요청 처리 중(202 반환 전) 동기적으로 호출한다 — 실패 시 422 `ApiError`를 raise한다."""
    _derive(common_spec, provider_spec)


def build_tfvars(job_id: int, workspace_name: str, common: _CommonSpec, provider: _ProviderSpec) -> dict:
    # job_id를 안 붙이면 같은 common_spec.name으로 여러 번 시도할 때(예: 이전 시도가 실패해 재시도할
    # 때) Front Door endpoint 이름이 겹쳐 "That resource name isn't available." Conflict가 난다
    # (2026-09-18 실측 — 모듈 주석은 "이름 자체의 전역 유일성은 신경 안 써도 된다"고 했었는데,
    # 실제로는 endpoint 이름 자체가 구독 범위에서 유일해야 하는 것으로 보인다). S3/Storage
    # Account와 같은 이유로 job_id를 붙여 매 job마다 새 이름을 쓴다.
    base = f"mcp-{common.name}-{job_id}"[:40]
    resource_group_name = (provider.resource_group or f"rg-cdn-{workspace_name}")[:89]

    # https_redirect가 켜져 있으면 azurerm이 supported_protocols에 Http/Https 둘 다 요구한다
    # (모듈 docstring·main.tf 주석 참고) — 사용자의 원래 선택은 forwarding_protocol에만 반영한다.
    if provider.https_redirect:
        supported_protocols = ["Http", "Https"]
    else:
        supported_protocols = ["Https"] if provider.protocol == "https_only" else ["Http", "Https"]
    forwarding_protocol = "HttpsOnly" if provider.protocol == "https_only" else "MatchRequest"

    # Front Door health_probe.interval_in_seconds 허용 범위(Azure 문서 기준) — clamp.
    interval = max(1, min(255, provider.health_probe_interval_seconds))

    return {
        "resource_group_name": resource_group_name,
        "use_existing_resource_group": provider.use_existing_resource_group,
        # Front Door 리소스 자체는 global이지만 리소스 그룹엔 location이 필요하다 — 다른 Azure
        # 러너들과 같은 기본 리전에 고정한다(사용자 입력 없음, CDN은 지역 선택 UI가 아예 없다).
        "location": "koreacentral",
        "profile_name": f"{base}-fd-profile"[:64],
        "endpoint_name": f"{base}-ep"[:64],
        "origin_group_name": f"{base}-og"[:64],
        "origin_name": f"{base}-origin"[:64],
        "route_name": f"{base}-route"[:64],
        "origin_host_name": provider.origin,
        "health_probe_path": provider.health_probe_path or "/",
        "health_probe_interval_seconds": interval,
        "supported_protocols": supported_protocols,
        "forwarding_protocol": forwarding_protocol,
        "https_redirect_enabled": provider.https_redirect,
        "query_string_caching_behavior": provider.query_string_caching_behavior,
        "compression_enabled": provider.compression,
        "content_types_to_compress": _CONTENT_TYPES_TO_COMPRESS,
        "tags": {**common.tags, "managed-by": "multi-cloud-platform", "job-id": str(job_id)},
    }


def _credential_env(secret_payload: dict, project_id: str | None) -> dict[str, str]:
    required = ("tenant_id", "client_id", "client_secret")
    missing = [f for f in required if not secret_payload.get(f)]
    if not project_id:
        missing.append("subscription_id(cloud_account.external_account_id)")
    if missing:
        raise ApiError(
            422,
            "CREDENTIAL_VERIFICATION_FAILED",
            f"credential에 Azure 인증에 필요한 필드가 없습니다: {', '.join(missing)}",
        )
    return {
        "ARM_TENANT_ID": secret_payload["tenant_id"],
        "ARM_CLIENT_ID": secret_payload["client_id"],
        "ARM_CLIENT_SECRET": secret_payload["client_secret"],
        # 구독 ID는 secret_payload에 없다 — cloud_accounts.external_account_id로 저장되고
        # 라우터가 project_id로 넘긴다(2026-09-15 발견·수정, 다른 Azure 러너 3개와 동일 패턴).
        "ARM_SUBSCRIPTION_ID": project_id,
    }


def run(
    *,
    job_id: int,
    workspace_dir: Path,
    common_spec: dict,
    provider_spec: dict,
    secret_payload: dict,
    workspace_name: str | None = None,
    project_id: str | None = None,  # 라우터가 account.external_account_id를 넘긴다 — Azure 구독
    # ID로 쓴다(_credential_env() 참고).
    cancel_check: Callable[[], bool] = lambda: False,
) -> TerraformResult:
    """백그라운드 job에서 호출된다 — raise 대신 `TerraformResult`로 실패를 표현한다."""
    try:
        common, provider = _derive(common_spec, provider_spec)
        credential_env = _credential_env(secret_payload, project_id)
    except ApiError as exc:
        return TerraformResult(success=False, error_code=exc.code, error_message=exc.message)

    ws_name = workspace_name or workspace_dir.name
    tfvars = build_tfvars(job_id, ws_name, common, provider)
    secrets = [secret_payload["client_secret"], secret_payload["tenant_id"], secret_payload["client_id"]]

    return run_apply(
        workspace_dir, MODULE_DIR, tfvars, credential_env, secrets=secrets, cancel_check=cancel_check
    )
