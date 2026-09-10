"""API 명세서 v1.1 §14 audit_events 기록 헬퍼.

`audit_events.metadata_json`에는 비밀번호·JWT·클라우드 secret·복호화된 payload를 절대 넣지
않는다(models.py의 AuditEvent 문서화 주석 참고). 여기서는 흔한 실수를 막기 위해 알려진 민감
키를 한 번 더 걸러낸다(1차 방어는 호출부가 애초에 그런 값을 넘기지 않는 것).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent

_METADATA_KEY_DENYLIST = {
    "secret",
    "secret_payload",
    "password",
    "access_key_id",
    "secret_access_key",
    "session_token",
    "client_secret",
    "private_key",
    "private_key_id",
    "token",
    "authorization",
    "public_identifier",
}


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    return {k: v for k, v in metadata.items() if k.lower() not in _METADATA_KEY_DENYLIST}


def record_audit_event(
    db: Session,
    *,
    actor_user_id: int | None,
    action: str,
    target_type: str,
    target_id: str | None,
    result: str,
    provider: str | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            result=result,
            provider=provider,
            metadata_json=_sanitize_metadata(metadata),
            request_id=request_id,
        )
    )
