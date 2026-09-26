"""Azure 자격증명 검증의 **진단 기록**(app/providers/azure.py) — 네트워크를 쓰지 않는다.

확인하는 것 두 가지다.
  1) 실패 단계가 갈리는가 — payload / token / subscription.
  2) 기록에 **남기면 안 되는 것**이 남지 않는가 — 원문 메시지·응답 본문·헤더·시크릿·토큰.

⚠️ 반환 계약은 이번 변경 대상이 아니다: 어느 단계에서 실패하든 `verified=False`,
`error_code="PROVIDER_AUTHENTICATION_FAILED"`, `permission_scope`는 기본값 그대로여야 한다.
"""

from __future__ import annotations

import logging

import pytest
from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError

import app.providers.azure as azure_provider

SECRET = {"tenant_id": "t", "client_id": "c", "client_secret": "super-secret-value-do-not-log"}
SUB = "00000000-1111-2222-3333-444455556666"


class _Records(logging.Handler):
    """**실제 로거(`mcp.app`)**에 붙어 파일로 나갈 그 줄을 그대로 받는다(파일 대신 메모리).

    `app.log`에 쓰이는 것과 같은 포맷터·같은 경로를 지나므로, 여기 없는 문자열은 파일에도 없다."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture()
def logs(monkeypatch):
    import json

    handler = _Records()
    logger = logging.getLogger("mcp.app")       # logging_config._make_logger가 쓰는 실제 이름
    logger.addHandler(handler)

    # 필드 단위로도 보기 쉽게 호출 인자를 따로 모은다(줄 검사는 위 handler.lines로 한다).
    handler.payloads: list[dict] = []
    original = azure_provider.log_business_event

    def _spy(event, **fields):
        handler.payloads.append(dict(fields, event=event))
        return original(event, **fields)

    monkeypatch.setattr(azure_provider, "log_business_event", _spy)
    yield handler
    logger.removeHandler(handler)


def _credential_raising(exc):
    def _factory(**kwargs):
        raise exc
    return _factory


class _Credential:
    """토큰 단계를 흉내 낸다 — 네트워크로 나가지 않는다."""

    def __init__(self, token_error=None):
        self.token_error = token_error
        self.token_calls = 0

    def get_token(self, *scopes, **kwargs):
        self.token_calls += 1
        if self.token_error is not None:
            raise self.token_error
        from azure.core.credentials import AccessToken
        return AccessToken("stub-token-value", 4102444800)


def _http_error(status: int, code: str | None, message: str = "boom"):
    exc = HttpResponseError(message=message)
    exc.status_code = status
    if code is not None:
        exc.error = type("E", (), {"code": code, "message": message})()
    return exc


def _install(monkeypatch, *, credential=None, credential_error=None, subscription_error=None):
    if credential_error is not None:
        monkeypatch.setattr(azure_provider, "ClientSecretCredential", _credential_raising(credential_error))
    else:
        cred = credential or _Credential()
        monkeypatch.setattr(azure_provider, "ClientSecretCredential", lambda **kw: cred)

    class _Subscriptions:
        def get(self, subscription_id):
            if subscription_error is not None:
                raise subscription_error
            return object()

    class _SubscriptionClient:
        def __init__(self, credential):
            self.subscriptions = _Subscriptions()

    monkeypatch.setattr(azure_provider, "SubscriptionClient", _SubscriptionClient)

    class _ResourceClient:                    # inventory_read 프로빙 — 성공 경로에서만 쓰인다
        def __init__(self, credential, subscription_id):
            self.resources = type("R", (), {"list": staticmethod(lambda: iter([object()]))})()

    monkeypatch.setattr(azure_provider, "ResourceManagementClient", _ResourceClient)


def _only(payloads):
    failures = [p for p in payloads if p.get("event") == "credential.verify.failed"]
    assert len(failures) == 1, failures
    return failures[0]


# --- 단계 구분 ----------------------------------------------------------------------------------


def test_missing_field_is_payload_stage(monkeypatch, logs):
    _install(monkeypatch)

    result = azure_provider.verify(SUB, {"tenant_id": "t"})       # client_id/secret 없음 → KeyError

    assert result.verified is False and result.error_code == "PROVIDER_AUTHENTICATION_FAILED"
    rec = _only(logs.payloads)
    assert rec["stage"] == "payload" and rec["exception"] == "KeyError"


def test_token_stage_failure_records_aadsts_identifier(monkeypatch, logs):
    exc = ClientAuthenticationError(
        message="A configuration issue is preventing authentication - AADSTS7000215: "
                "Invalid client secret provided. secret=super-secret-value-do-not-log")
    _install(monkeypatch, credential=_Credential(token_error=exc))

    result = azure_provider.verify(SUB, SECRET)

    assert result.verified is False and result.error_code == "PROVIDER_AUTHENTICATION_FAILED"
    rec = _only(logs.payloads)
    assert rec["stage"] == "token"
    assert rec["exception"] == "ClientAuthenticationError"
    assert rec["azure_error_code"] == "AADSTS7000215"


@pytest.mark.parametrize("status,code", [
    (403, "AuthorizationFailed"),
    (404, "SubscriptionNotFound"),
    (401, "InvalidAuthenticationTokenTenant"),
])
def test_subscription_stage_failure_records_status_and_code(monkeypatch, logs, status, code):
    _install(monkeypatch, subscription_error=_http_error(status, code))

    result = azure_provider.verify(SUB, SECRET)

    assert result.verified is False and result.error_code == "PROVIDER_AUTHENTICATION_FAILED"
    # 토큰은 받았는데 구독 조회에서 막힌 경우다 — 이걸 토큰 실패와 섞지 않는다.
    rec = _only(logs.payloads)
    assert rec["stage"] == "subscription" and rec["http_status"] == status and rec["azure_error_code"] == code


def test_unknown_arm_error_code_is_not_echoed(monkeypatch, logs):
    """허용 목록 밖 코드는 값을 그대로 남기지 않는다."""
    _install(monkeypatch, subscription_error=_http_error(400, "SomeUndocumentedInternalCode"))

    azure_provider.verify(SUB, SECRET)

    rec = _only(logs.payloads)
    assert rec["azure_error_code"] == "other"
    assert "SomeUndocumentedInternalCode" not in " ".join(logs.lines)


def test_generic_azure_error_without_status_or_code(monkeypatch, logs):
    _install(monkeypatch, subscription_error=AzureError("network is unreachable"))

    azure_provider.verify(SUB, SECRET)

    rec = _only(logs.payloads)
    assert rec["stage"] == "subscription" and rec["exception"] == "AzureError"
    assert rec["http_status"] is None and rec["azure_error_code"] is None


# --- 기록해서는 안 되는 것 --------------------------------------------------------------------


def test_secret_and_message_never_reach_the_log(monkeypatch, logs):
    """예외 메시지에 시크릿·응답 본문처럼 보이는 문자열을 심어도 로그로 새지 않아야 한다."""
    leak = "super-secret-value-do-not-log"
    exc = _http_error(403, "AuthorizationFailed",
                      message=f"The client 'app' with secret {leak} and token eyJhbGciOiJIUzI1NiJ9.payload "
                              f"does not have authorization. body={{'error':{{'message':'{leak}'}}}}")
    _install(monkeypatch, subscription_error=exc)

    azure_provider.verify(SUB, SECRET)

    blob = " ".join(logs.lines)
    assert "credential.verify.failed" in blob      # 실제 로거를 통해 줄이 남았는지부터 확인한다
    assert leak not in blob
    assert "eyJhbGciOiJIUzI1NiJ9" not in blob          # 토큰처럼 보이는 문자열도 남지 않는다
    assert "does not have authorization" not in blob   # 원문 메시지도 남지 않는다
    rec = _only(logs.payloads)
    assert set(rec) == {"event", "provider", "stage", "exception", "http_status", "azure_error_code", "level"}


def test_aadsts_extraction_is_length_and_shape_limited():
    """추출은 형식(AADSTS+숫자)과 길이로 제한한다 — 자릿수는 공식 표에 5·6·7자리가 모두 있다."""
    long_tail = ClientAuthenticationError(message="AADSTS1234567890123 and more text " + "x" * 500)
    value = azure_provider._aadsts_code(long_tail)

    assert value is not None and len(value) <= azure_provider._MAX_ERROR_IDENTIFIER_LEN
    assert value.startswith("AADSTS") and value[6:].isdigit()
    assert azure_provider._aadsts_code(ClientAuthenticationError(message="AADSTS12 too short")) is None
    assert azure_provider._aadsts_code(ClientAuthenticationError(message="no code here")) is None


# --- 성공 경로 회귀 ------------------------------------------------------------------------------


def test_success_path_is_unchanged(monkeypatch, logs):
    cred = _Credential()
    _install(monkeypatch, credential=cred)

    result = azure_provider.verify(SUB, SECRET)

    assert result.verified is True and result.error_code is None
    assert result.permission_scope["inventory_read"] is True
    assert result.permission_scope["cost_read"] is False       # 프로빙하지 않는 값 — 그대로 둔다
    assert cred.token_calls == 1                               # 단계 구분을 위해 명시적으로 한 번 받는다
    assert [p for p in logs.payloads if p.get("event") == "credential.verify.failed"] == []
