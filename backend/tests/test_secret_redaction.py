"""완료 기준(§20-4): secret이 응답·오류·access.log/app.log·audit_events.metadata_json 어디에도
노출되지 않는지 확인한다. 실제 파일이 아니라 로거/저장 함수 단위로 검증해 테스트를 결정적으로 만든다.
"""

from __future__ import annotations

import logging

from app.audit import record_audit_event
from app.logging_config import access_logger, app_logger, log_access, log_business_event
from app.models import AuditEvent

SECRET_VALUE = "s3cr3t-should-never-leak"


def _capture(logger: logging.Logger):
    messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: messages.append(record.getMessage())
    logger.addHandler(handler)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)


def test_log_business_event_redacts_known_secret_keys():
    gen = _capture(app_logger)
    messages = next(gen)
    log_business_event(
        "credential.verify",
        provider="aws",
        secret_payload={"secret_access_key": SECRET_VALUE},
    )
    next(gen, None)

    assert len(messages) == 1
    assert SECRET_VALUE not in messages[0]


def test_log_access_redacts_if_sensitive_field_is_ever_passed():
    gen = _capture(access_logger)
    messages = next(gen)
    log_access(
        request_id="req-1", method="POST", path="/api/v1/credentials/aws", status=201,
        duration_ms=1.2, user_id=1, authorization=f"Bearer {SECRET_VALUE}",
    )
    next(gen, None)

    assert len(messages) == 1
    assert SECRET_VALUE not in messages[0]


def test_log_business_event_still_emits_after_being_externally_disabled():
    """alembic/env.py는 logging.config.fileConfig()를 기본 옵션(disable_existing_loggers=True)으로
    호출한다 — 마이그레이션이 한 번이라도 실행되면(예: test_migration_backfill.py) 그 이전에
    만들어진 모든 로거가 조용히 꺼진다. log_business_event/log_access가 매 호출마다 스스로
    다시 켜는지 확인한다."""
    app_logger.disabled = True
    try:
        gen = _capture(app_logger)
        messages = next(gen)
        log_business_event("credential.verify", provider="aws")
        next(gen, None)

        assert len(messages) == 1
    finally:
        app_logger.disabled = False


def test_audit_event_metadata_strips_denylisted_keys(db_session):
    record_audit_event(
        db_session,
        actor_user_id=None,
        action="credential.create",
        target_type="credential",
        target_id="1",
        result="requested",
        provider="aws",
        metadata={"secret_payload": {"secret_access_key": SECRET_VALUE}, "error_code": "PROVIDER_API_ERROR"},
    )
    db_session.flush()

    stored = db_session.query(AuditEvent).one()
    assert "secret_payload" not in stored.metadata_json
    assert stored.metadata_json["error_code"] == "PROVIDER_API_ERROR"
