"""Azure 어댑터를 **설치된 실제 SDK 경로**로 검증한다 — 네트워크는 쓰지 않는다.

가짜 클라이언트를 만들지 않는다. 진짜 `CostManagementClient`가 진짜 직렬화·파이프라인을 태우고,
**인증(토큰)과 HTTP transport만** 대체한다. 그래서 다음이 실제로 검증된다:
요청 URL·본문(기간·그룹·집계)·nextLink 후속 요청·오류 매핑·요청 횟수.

가짜 클라이언트에만 있는 메서드로 통과하는 일이 없도록, 이 파일은 `azure_cost` 모듈의
`CostManagementClient`를 **바꾸지 않는다**(ClientSecretCredential만 바꾼다).
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
import requests
from azure.core.pipeline.transport import HttpTransport, RequestsTransportResponse
from azure.core.rest import HttpRequest as RestHttpRequest
from azure.core.rest._requests_basic import RestRequestsTransportResponse

import app.cost.azure_cost as az
from app.config import get_settings
from app.cost.azure_cost import AzureCostProvider

SECRET = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}
SUB = "00000000-1111-2222-3333-444455556666"
START = dt.date(2026, 9, 1)
END = dt.date(2026, 9, 4)
COLUMNS = ["PreTaxCost", "UsageDate", "Currency", "ServiceName", "ChargeType"]


def _body(rows, next_link=None, columns=None):
    return {"properties": {
        "columns": [{"name": c, "type": "String"} for c in (columns or COLUMNS)],
        "rows": rows, "nextLink": next_link}}


def _request_body(request) -> dict:
    """transport 계층 요청(HttpRequest)의 본문 — 구버전은 `body`, rest 계층은 `content`다."""
    raw = getattr(request, "body", None)
    if raw is None:
        raw = getattr(request, "content", None)
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


def _fake_response(request, status_code, payload=None, headers=None):
    """실제 transport가 만드는 것과 같은 응답 객체를 돌려준다 — 손으로 흉내 내지 않는다.

    첫 페이지는 SDK 연산이 transport 계층 응답을(`RequestsTransportResponse`), nextLink 추적은
    rest 계층 응답을(`RestRequestsTransportResponse`) 기대한다. 실제 `RequestsTransport`가 요청
    타입에 따라 고르는 것과 같은 규칙을 그대로 쓴다."""
    raw = requests.Response()
    raw.status_code = status_code
    raw._content = json.dumps(payload).encode() if payload is not None else b""
    raw._content_consumed = True
    raw.encoding = "utf-8"
    raw.reason = "OK" if status_code < 400 else "Error"
    raw.headers.update({"Content-Type": "application/json"})
    raw.headers.update(headers or {})
    if isinstance(request, RestHttpRequest):
        return RestRequestsTransportResponse(request=request, internal_response=raw)
    return RequestsTransportResponse(request, raw)


class _RecordingTransport(HttpTransport):
    """보낸 요청을 기록하고 정해진 응답을 돌려준다. 실제 네트워크 없음."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("예상보다 많은 요청이 나갔습니다")
        status, payload, headers = self._responses.pop(0)
        return _fake_response(request, status, payload, headers)

    def open(self): pass
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class _FakeCredential:
    """토큰 발급은 **비용 API transport를 쓰지 않는다**(실제 credential도 자기 파이프라인을 쓴다).
    몇 번 불렸는지 세어 두고, 그 횟수가 api_calls에 섞이지 않는 것을 테스트가 확인한다."""

    def __init__(self):
        self.token_calls = 0

    def get_token(self, *scopes, **kwargs):
        from azure.core.credentials import AccessToken
        self.token_calls += 1
        return AccessToken("fake-token", int(dt.datetime.now(dt.timezone.utc).timestamp()) + 3600)

    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


@pytest.fixture()
def sdk(monkeypatch):
    """실제 CostManagementClient를 쓰되 인증·transport만 대체한다."""
    holder = {}

    def _install(responses, **settings_env):
        for k, v in settings_env.items():
            monkeypatch.setenv(k, str(v))
        get_settings.cache_clear()
        transport = _RecordingTransport(responses)
        holder["transport"] = transport
        credential = _FakeCredential()
        holder["credential"] = credential
        monkeypatch.setattr(az, "ClientSecretCredential", lambda **kw: credential)
        # transport는 **어댑터가 감싸는 안쪽**만 바꾼다 — 상한을 강제하는 _BudgetedTransport를
        # 통째로 치우면 정작 검증하려는 그 코드를 건너뛰게 된다.
        monkeypatch.setattr(az, "RequestsTransport", lambda *a, **kw: transport)
        return holder

    yield _install
    get_settings.cache_clear()


def _fetch(sleeper=None):
    return AzureCostProvider(sleeper=sleeper or (lambda s: None)).fetch(SECRET, SUB, START, END)


# --- 실제 직렬화된 요청 본문 --------------------------------------------------------------------


def test_real_request_url_and_body(sdk):
    h = sdk([(200, _body([[1.0, 20260901, "USD", "VM", "Usage"]]), {})])

    r = _fetch()

    req = h["transport"].requests[0]
    assert req.method == "POST"
    assert req.url.startswith(f"https://management.azure.com/subscriptions/{SUB}/providers/Microsoft.CostManagement/query")
    assert "api-version=2022-10-01" in req.url
    body = _request_body(req)
    assert body["type"] == "ActualCost" and body["timeframe"] == "Custom"
    assert body["timePeriod"]["from"].startswith("2026-09-01") and body["timePeriod"]["to"].startswith("2026-09-04")
    assert body["dataset"]["granularity"] == "Daily"
    assert [g["name"] for g in body["dataset"]["grouping"]] == ["ServiceName", "ChargeType"]
    assert body["dataset"]["aggregation"]["totalCost"] == {"name": "PreTaxCost", "function": "Sum"}
    assert r.partial is False and r.api_calls == 1 and len(r.rows) == 1


def test_real_next_link_is_followed_with_same_body(sdk):
    link = "https://management.azure.com/subscriptions/x/providers/Microsoft.CostManagement/query?api-version=2022-10-01&$skiptoken=AAA"
    h = sdk([
        (200, _body([[1.0, 20260901, "USD", "VM", "Usage"]], next_link=link), {}),
        (200, _body([[2.0, 20260902, "USD", "VM", "Usage"]]), {}),
    ])

    r = _fetch()

    assert [str(x.amount) for x in r.rows] == ["1.0", "2.0"]
    assert r.api_calls == 2 and len(h["transport"].requests) == 2
    assert h["transport"].requests[1].url == link
    assert _request_body(h["transport"].requests[1])["type"] == "ActualCost"   # 같은 본문을 보낸다


def test_real_204_is_empty_not_error(sdk):
    sdk([(204, None, {})])

    r = _fetch()

    assert r.partial is False and r.rows == [] and r.currency is None
    assert r.coverage_basis == "observed_only" and r.covered_through is None


@pytest.mark.parametrize("status,expected", [
    (403, "CLOUD_PERMISSION_DENIED"),
    (500, "PROVIDER_API_ERROR"),
    (401, "PROVIDER_AUTHENTICATION_FAILED"),
])
def test_real_error_status_mapping(sdk, status, expected):
    sdk([(status, {"error": {"code": "x", "message": "y"}}, {})])

    r = _fetch()

    assert r.partial is True and r.error_code == expected


def test_real_429_waits_exactly_what_server_asked(sdk):
    slept = []
    sdk([
        (429, {"error": {}}, {az.RETRY_AFTER_HEADERS[0]: "7"}),
        (200, _body([[1.0, 20260901, "USD", "VM", "Usage"]]), {}),
    ])

    r = AzureCostProvider(sleeper=slept.append).fetch(SECRET, SUB, START, END)

    assert slept == [7.0]            # 서버가 요구한 시간 그대로 — 더 일찍 재시도하지 않는다
    assert r.partial is False and r.api_calls == 2


def test_real_request_budget_is_enforced(sdk):
    """제한 실수집용 상한: 최대 2페이지·전체 3요청. 남은 페이지가 있으면 성공으로 끝내지 않는다."""
    link1 = "https://management.azure.com/q?api-version=2022-10-01&$skiptoken=A"
    link2 = "https://management.azure.com/q?api-version=2022-10-01&$skiptoken=B"
    h = sdk([
        (200, _body([[1.0, 20260901, "USD", "VM", "Usage"]], next_link=link1), {}),
        (200, _body([[2.0, 20260902, "USD", "VM", "Usage"]], next_link=link2), {}),
        (200, _body([[3.0, 20260903, "USD", "VM", "Usage"]], next_link=None), {}),
    ], COST_AZURE_MAX_PAGES=2, COST_AZURE_MAX_REQUESTS=3)

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"    # 성공으로 끝내지 않는다
    assert len(h["transport"].requests) == 2                              # 3번째 페이지는 아예 안 보냈다
    assert r.api_calls == 2


def test_real_request_budget_counts_retries(sdk):
    """429 재시도도 요청 상한에 포함된다 — 상한을 넘기기 전에 멈춘다."""
    sdk([
        (429, {"error": {}}, {"Retry-After": "1"}),
        (429, {"error": {}}, {"Retry-After": "1"}),
    ], COST_AZURE_MAX_REQUESTS=2)

    r = AzureCostProvider(sleeper=lambda s: None).fetch(SECRET, SUB, START, END)

    assert r.partial is True and r.api_calls == 2


# --- 실제 전송 횟수 = api_calls (리다이렉트·재시도 포함) -----------------------------------------


LINK = "https://management.azure.com/subscriptions/x/providers/Microsoft.CostManagement/query?api-version=2022-10-01&$skiptoken=AAA"
PAGE1 = (200, _body([[1.0, 20260901, "USD", "VM", "Usage"]], next_link=LINK), {})


def test_redirect_is_not_followed_and_is_not_empty(sdk):
    """3xx는 따라가지도(추가 요청 0) 빈 응답으로 처리하지도 않는다 — 오류다.

    **307을 쓰는 이유**: azure-core RedirectPolicy는 301/302는 GET/HEAD에서만 따라가고
    (`_redirect.py:126-132`), 303·307·308·300은 메서드와 무관하게 따라간다. 우리 요청은 POST라
    실제로 추가 요청이 나갈 수 있는 쪽은 307 계열이다."""
    h = sdk([(307, None, {"Location": "https://elsewhere.example/q"})])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"
    assert r.rows == [] and len(h["transport"].requests) == 1 and r.api_calls == 1


def test_redirect_on_next_link_page_is_error_too(sdk):
    """첫 페이지만이 아니라 후속 페이지(우리가 직접 보내는 요청)에도 같은 규칙이 적용된다."""
    h = sdk([PAGE1, (307, None, {"Location": "https://elsewhere.example/q"})])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"
    assert len(h["transport"].requests) == 2 and r.api_calls == 2
    assert [str(x.amount) for x in r.rows] == ["1.0"]      # 1페이지 행은 들고 오되 partial이라 저장되지 않는다


@pytest.mark.parametrize("status,headers,expected", [
    (401, {}, "PROVIDER_AUTHENTICATION_FAILED"),     # 후속 페이지도 첫 페이지와 같은 분류여야 한다
    (403, {}, "CLOUD_PERMISSION_DENIED"),
    (429, {}, "PROVIDER_RATE_LIMITED"),              # 대기 안내가 없으면 임의로 더 두드리지 않는다
    (500, {}, "PROVIDER_API_ERROR"),
])
def test_next_page_error_mapping(sdk, status, headers, expected):
    h = sdk([PAGE1, (status, {"error": {"code": "x", "message": "y"}}, headers)])

    r = _fetch()

    assert r.partial is True and r.error_code == expected
    assert len(h["transport"].requests) == 2 and r.api_calls == 2
    # 실패해도 1페이지 행을 버리지 않는다 — 저장 여부는 호출부가 partial로 판단한다
    # (수집 경로에서 기존 행·근거가 보존되는 것은 test_cost_ingest_gating.py의
    #  test_bad_second_page_preserves_existing_rows_and_basis가 고정한다).
    assert [str(x.amount) for x in r.rows] == ["1.0"]


def test_next_page_429_waits_then_succeeds(sdk):
    slept = []
    h = sdk([
        PAGE1,
        (429, {"error": {}}, {az.RETRY_AFTER_HEADERS[0]: "3"}),
        (200, _body([[2.0, 20260902, "USD", "VM", "Usage"]]), {}),
    ])

    r = AzureCostProvider(sleeper=slept.append).fetch(SECRET, SUB, START, END)

    assert slept == [3.0]
    assert r.partial is False and [str(x.amount) for x in r.rows] == ["1.0", "2.0"]
    assert len(h["transport"].requests) == 3 and r.api_calls == 3


def test_send_count_equals_api_calls_with_retry_and_redirect(sdk):
    """세는 지점이 transport라서, 재시도·3xx가 섞여도 실제 전송 횟수와 api_calls가 어긋나지 않는다."""
    h = sdk([
        (429, {"error": {}}, {"Retry-After": "1"}),
        PAGE1,
        (307, None, {"Location": "https://elsewhere.example/q"}),
    ])

    r = AzureCostProvider(sleeper=lambda s: None).fetch(SECRET, SUB, START, END)

    assert r.api_calls == len(h["transport"].requests) == 3


def test_token_requests_are_not_counted(sdk):
    """토큰 발급은 credential 자신의 경로다 — 비용 API 전송 수에 섞이지 않는다."""
    h = sdk([(200, _body([[1.0, 20260901, "USD", "VM", "Usage"]]), {})])

    r = _fetch()

    assert h["credential"].token_calls >= 1                      # 실제로 토큰을 받았고
    assert r.api_calls == len(h["transport"].requests) == 1      # 그래도 비용 요청은 1건뿐이다
    assert all("management.azure.com" in str(q.url) for q in h["transport"].requests)


def test_budget_blocks_before_the_extra_send(sdk):
    """상한 검사는 보내기 **전에** 한다 — 초과 요청이 실제로 나가지 않는다(재시도 경로 포함)."""
    h = sdk([
        (429, {"error": {}}, {"Retry-After": "1"}),
        (200, _body([[1.0, 20260901, "USD", "VM", "Usage"]]), {}),   # 이 응답은 쓰이지 않아야 한다
    ], COST_AZURE_MAX_REQUESTS=1)

    r = AzureCostProvider(sleeper=lambda s: None).fetch(SECRET, SUB, START, END)

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"
    assert len(h["transport"].requests) == 1 and r.api_calls == 1
