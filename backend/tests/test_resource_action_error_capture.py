"""리소스 액션 실행 시 SDK 예외 원문을 secret 제거 후 캡처하는지 검증(5번 작업).

`resource_actions.perform_action`이 provider 어댑터에서 나온 원문을 어디서(catch-all / __cause__)
잡든 secret을 지우고 message에 담아야, 라우터가 이를 구체 원인으로 번역할 수 있다.
"""

from __future__ import annotations

import app.providers.aws as aws_provider
from app.resource_actions import ResourceActionError, perform_action

_SECRET = "AKIAsecretkey1234567890"  # MIN_REDACT_LENGTH(8) 이상 → redact 대상


def _call():
    perform_action(
        provider="aws",
        service_code="ec2",
        original_resource_type="EC2 Instance",
        action="stop",
        secret_payload={"access_key_id": _SECRET},
        external_account_id="1234",
        region="ap-northeast-2",
        external_resource_id="i-1",
    )


def test_raw_exception_is_wrapped_with_redacted_message(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError(f"AccessDenied for key {_SECRET}: not authorized")

    monkeypatch.setattr(aws_provider, "perform_resource_action", _boom)

    try:
        _call()
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "PROVIDER_API_ERROR"
        assert err.message and "AccessDenied" in err.message  # 원문 보존
        assert _SECRET not in err.message  # secret은 제거


def test_wrapped_error_captures_message_from_cause(monkeypatch):
    def _boom(*args, **kwargs):
        try:
            raise RuntimeError(f"quota exceeded, secret={_SECRET}")
        except RuntimeError as exc:
            raise ResourceActionError("PROVIDER_API_ERROR") from exc

    monkeypatch.setattr(aws_provider, "perform_resource_action", _boom)

    try:
        _call()
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "PROVIDER_API_ERROR"
        assert err.message and "quota exceeded" in err.message
        assert _SECRET not in err.message


def test_precheck_error_has_no_message(monkeypatch):
    # 지원하지 않는 동작은 어댑터를 부르기 전에 막히므로 message가 없다.
    err = None
    try:
        perform_action(
            provider="aws", service_code="s3", original_resource_type="S3 Bucket",
            action="stop",  # S3는 delete만 지원
            secret_payload={"access_key_id": _SECRET},
            external_account_id="1234", region=None, external_resource_id="b-1",
        )
    except ResourceActionError as exc:
        err = exc
    assert err is not None
    assert err.code == "UNSUPPORTED_OPERATION"
    assert err.message is None
