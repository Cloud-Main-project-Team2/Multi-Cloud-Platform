"""CSP 자격 증명 실검증 진입점.

API 명세서 v1.1 §6.2/§6.8: credential 저장 직후(그리고 재검증 시) 실제 CSP API로 신원을
확인하고 `permission_scope`를 채운다. 신원 확인(AWS `sts:GetCallerIdentity`, Azure
`SubscriptionClient.subscriptions.get`, GCP `projects.get`)이 성공한 뒤에만 가벼운 읽기 API로
권한 범위를 "best-effort" 프로빙한다 — 프로빙 자체가 실패해도(권한 없음 포함) verified 여부에는
영향을 주지 않고 해당 permission_scope 항목만 False로 남는다.

**이번 세션의 의도적 축소(CLAUDE.md에 기록)**: `inventory_read`는 AWS/Azure/GCP 모두 실제
가벼운 목록 조회로 프로빙한다. `cost_read`는 AWS만 Cost Explorer로 프로빙하고 Azure/GCP는
False 고정(§19에 이미 "GCP 실제 비용 수집 방식·권한"이 미확정 항목으로 남아 있어 이 세션에서
새로 정하지 않는다). `resource_control`/`provision`은 AWS만 `iam:SimulatePrincipalPolicy`로
프로빙하고 Azure/GCP는 False 고정 — 안전하게 "읽기 전용" 방식으로 판별할 표준 API가 각 SDK에
따로 필요해 범위를 넘는다. 사용자가 화면의 자기신고 체크박스(정적 UI, 이번 세션에서 변경하지
않음)로 나머지를 보완하는 것을 전제로 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.errors import ApiError

PROVIDERS = ("aws", "azure", "gcp")

_DEFAULT_SCOPE = {
    "inventory_read": False,
    "resource_control": False,
    "provision": False,
    "cost_read": False,
}

REQUIRED_SECRET_FIELDS: dict[str, set[str]] = {
    "aws": {"access_key_id", "secret_access_key"},
    "azure": {"client_id", "client_secret", "tenant_id"},
    "gcp": {"type", "client_email", "private_key_id", "private_key"},
}


@dataclass
class VerificationResult:
    verified: bool
    permission_scope: dict[str, bool] = field(default_factory=lambda: dict(_DEFAULT_SCOPE))
    error_code: str | None = None


def validate_secret_payload(provider: str, secret_payload: dict) -> None:
    required = REQUIRED_SECRET_FIELDS[provider]
    missing = sorted(required - secret_payload.keys())
    if missing:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            f"{provider} secret_payload에 필수 필드가 없습니다.",
            details=[{"field": f"secret_payload.{f}", "reason": "required"} for f in missing],
        )


def verify_credential(provider: str, external_account_id: str, secret_payload: dict) -> VerificationResult:
    from app.providers import aws as aws_provider
    from app.providers import azure as azure_provider
    from app.providers import gcp as gcp_provider

    module = {"aws": aws_provider, "azure": azure_provider, "gcp": gcp_provider}[provider]
    try:
        return module.verify(external_account_id, secret_payload)
    except Exception:  # noqa: BLE001 — provider adapter는 절대 원문 SDK 예외를 밖으로 흘리지 않는다(§18).
        return VerificationResult(verified=False, error_code="PROVIDER_API_ERROR")
