"""Azure Cost Management 어댑터(app/cost/azure_cost.py) — **실제 Azure를 절대 호출하지 않는다.**

SDK 클라이언트를 통째로 스텁으로 갈아 끼우고 응답 모양만 흉내 낸다. 확인하는 것:
정상/다중 페이지·열 순서 변경·빈 응답(204)·오류 4종·중간 페이지 실패·잘못된 응답·날짜 경계·
음수 금액·알 수 없는 요금 종류·coverage_basis/covered_through 고정.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ServiceResponseError,
)

import app.cost.azure_cost as az
from app.config import get_settings
from app.cost.azure_cost import AzureCostProvider

SECRET = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}
SUB = "00000000-1111-2222-3333-444455556666"
START = dt.date(2026, 9, 1)
END = dt.date(2026, 9, 4)          # 제외 경계


def _page(rows, columns=None, next_link=None):
    columns = columns or ["PreTaxCost", "UsageDate", "Currency", "ServiceName", "ChargeType"]
    return {"columns": [{"name": c, "type": "String"} for c in columns], "rows": rows, "nextLink": next_link}


class _NoopTransport:
    """실제 전송은 하지 않지만 **어댑터의 _BudgetedTransport를 실제로 지나가게** 한다 —
    요청 수 집계·상한이 스텁 경로에서도 진짜 코드로 검증된다."""

    def send(self, request, **kwargs): return None
    def open(self): pass
    def close(self): pass
    def sleep(self, duration): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class _FakeQuery:
    def __init__(self, pages, error=None, transport=None):
        self._pages = list(pages)
        self.error = error
        self.calls = []
        self._transport = transport

    def usage(self, scope, parameters):
        if self._transport is not None:
            self._transport.send(object())      # 실제 SDK처럼 "보내는" 시점을 통과시킨다
        self.calls.append({"scope": scope, "parameters": parameters})
        if self.error is not None:
            raise self.error
        page = self._pages.pop(0)
        if page is None:
            return None                      # 204 No Content
        return _FakeQueryResult(page)


class _FakeQueryResult:
    def __init__(self, page):
        self.columns = [type("C", (), {"name": c["name"], "type": c["type"]})() for c in page["columns"]]
        self.rows = page["rows"]
        self.next_link = page.get("nextLink")


class _FakeClient:
    def __init__(self, pages, error=None, follow_pages=None, follow_error=None, **kwargs):
        self._transport = kwargs.get("transport")
        self.query = _FakeQuery(pages, error, transport=self._transport)
        self.kwargs = kwargs
        self._follow_pages = list(follow_pages or [])
        self._follow_error = follow_error
        self.follow_requests = []
        self.closed = False
        self._serialize = type("S", (), {"body": staticmethod(lambda obj, name: {"fake": "body"})})()

    def _send_request(self, request):
        if self._transport is not None:
            self._transport.send(request)
        self.follow_requests.append(request)
        if self._follow_error is not None:
            raise self._follow_error
        page = self._follow_pages.pop(0)
        return type("R", (), {
            "status_code": 200, "json": lambda self=None, p=page: {"properties": p},
        })()

    def close(self):
        self.closed = True


@pytest.fixture()
def patch_client(monkeypatch):
    holder = {}

    def _install(pages=(), error=None, follow_pages=None, follow_error=None, credential_error=None):
        monkeypatch.setattr(az, "ClientSecretCredential",
                            (lambda **kw: (_ for _ in ()).throw(credential_error)) if credential_error
                            else (lambda **kw: object()))

        def _factory(credential, **kwargs):
            holder["client"] = _FakeClient(pages, error, follow_pages, follow_error, **kwargs)
            return holder["client"]

        monkeypatch.setattr(az, "CostManagementClient", _factory)
        monkeypatch.setattr(az, "RequestsTransport", lambda *a, **kw: _NoopTransport())
        return holder

    return _install


def _fetch():
    return AzureCostProvider().fetch(SECRET, SUB, START, END)


# --- 정상 ---------------------------------------------------------------------------------------


def test_single_page_maps_rows_and_keeps_original_currency(patch_client):
    patch_client(pages=[_page([
        [1.5, 20260901, "KRW", "Virtual Machines", "Usage"],
        [-0.25, 20260902, "KRW", "Virtual Machines", "Refund"],
        [2.0, 20260903, "KRW", "Storage", "Purchase"],
    ])])

    r = _fetch()

    assert r.partial is False and r.error_code is None
    assert r.currency == "KRW"                         # 환산하지 않는다
    assert r.coverage_basis == "observed_only"         # A-2 고정
    assert r.covered_through is None
    assert r.api_calls == 1
    assert [(x.period_start, str(x.amount), x.charge_category, x.service, x.is_estimated) for x in r.rows] == [
        (dt.date(2026, 9, 1), "1.5", "usage", "Virtual Machines", True),
        (dt.date(2026, 9, 2), "-0.25", "refund", "Virtual Machines", True),   # 음수 보존
        (dt.date(2026, 9, 3), "2.0", "other", "Storage", True),               # Purchase는 usage가 아니다
    ]
    assert all(x.period_end == x.period_start + dt.timedelta(days=1) for x in r.rows)


def test_column_order_change_is_handled_by_name(patch_client):
    """열은 이름으로 찾는다 — 순서가 바뀌어도 값이 섞이지 않는다."""
    patch_client(pages=[_page(
        [["Usage", "USD", 20260901, 3.0, "Storage"]],
        columns=["ChargeType", "Currency", "UsageDate", "PreTaxCost", "ServiceName"],
    )])

    r = _fetch()

    assert [(str(x.amount), x.currency, x.service, x.charge_category) for x in r.rows] == [
        ("3.0", "USD", "Storage", "usage")
    ]


def test_aggregation_alias_column_name_is_accepted(patch_client):
    """계약 유형에 따라 금액 열 이름이 집계 별칭으로 올 수 있다 — 후보 목록으로 찾는다."""
    patch_client(pages=[_page([[4.0, 20260901, "USD", "VM", "Usage"]],
                              columns=["totalCost", "UsageDate", "Currency", "ServiceName", "ChargeType"])])

    r = _fetch()

    assert str(r.rows[0].amount) == "4.0" and r.partial is False


def test_multi_page_follows_next_link_and_counts_calls(patch_client):
    holder = patch_client(
        pages=[_page([[1.0, 20260901, "USD", "VM", "Usage"]],
                     next_link="https://management.azure.com/next?$skiptoken=AAA")],
        follow_pages=[{"columns": [{"name": c, "type": "String"} for c in
                                   ["PreTaxCost", "UsageDate", "Currency", "ServiceName", "ChargeType"]],
                       "rows": [[2.0, 20260902, "USD", "VM", "Usage"]], "nextLink": None}],
    )

    r = _fetch()

    assert [str(x.amount) for x in r.rows] == ["1.0", "2.0"]
    assert r.api_calls == 2 and r.partial is False
    assert holder["client"].follow_requests[0].url.startswith("https://management.azure.com/")


def test_next_link_to_unexpected_host_is_refused(patch_client):
    """응답이 준 URL을 그대로 믿고 토큰을 실어 보내지 않는다."""
    holder = patch_client(pages=[_page([[1.0, 20260901, "USD", "VM", "Usage"]],
                                       next_link="https://evil.example.com/next")])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"
    assert holder["client"].follow_requests == []       # 아예 보내지 않았다


def test_repeated_next_link_stops(patch_client):
    link = "https://management.azure.com/next?$skiptoken=SAME"
    patch_client(
        pages=[_page([[1.0, 20260901, "USD", "VM", "Usage"]], next_link=link)],
        follow_pages=[{"columns": [{"name": c, "type": "String"} for c in
                                   ["PreTaxCost", "UsageDate", "Currency", "ServiceName", "ChargeType"]],
                       "rows": [[1.0, 20260901, "USD", "VM", "Usage"]], "nextLink": link}],
    )

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


# --- 빈 응답 ------------------------------------------------------------------------------------


def test_empty_rows_is_not_promoted_to_confirmed_zero(patch_client):
    patch_client(pages=[_page([])])

    r = _fetch()

    assert r.rows == [] and r.partial is False
    assert r.coverage_basis == "observed_only"     # 빈 응답도 complete_range로 올리지 않는다
    assert r.covered_through is None
    assert r.currency is None                      # 통화를 모르면 USD를 붙이지 않는다


def test_204_no_content(patch_client):
    patch_client(pages=[None])

    r = _fetch()

    assert r.rows == [] and r.partial is False and r.coverage_basis == "observed_only"


# --- 오류 ---------------------------------------------------------------------------------------


def test_credential_failure(patch_client):
    patch_client(credential_error=ClientAuthenticationError("bad secret"))

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_AUTHENTICATION_FAILED" and r.rows == []


def test_permission_denied_403(patch_client):
    err = HttpResponseError(message="forbidden")
    err.status_code = 403
    patch_client(error=err)

    r = _fetch()

    assert r.partial is True and r.error_code == "CLOUD_PERMISSION_DENIED"


def test_rate_limited_429_waits_exactly_and_stops_at_request_budget(patch_client, monkeypatch):
    """서버가 요구한 시간만큼만 기다리고, 요청 상한에 닿으면 더 두드리지 않는다."""
    err = HttpResponseError(message="throttled")
    err.status_code = 429
    err.response = type("R", (), {"status_code": 429, "headers": {az.RETRY_AFTER_HEADERS[0]: "2"}})()
    monkeypatch.setenv("COST_AZURE_MAX_REQUESTS", "3")
    get_settings.cache_clear()
    slept = []
    holder = patch_client(error=err)

    r = AzureCostProvider(sleeper=slept.append).fetch(SECRET, SUB, START, END)

    assert r.partial is True and r.error_code in ("PROVIDER_RATE_LIMITED", "PROVIDER_API_ERROR")
    assert slept == [2.0, 2.0]                       # 서버가 요구한 값 그대로(더 짧게 자지 않는다)
    assert r.api_calls == 3                          # 상한만큼만 보냈다
    assert holder["client"].kwargs["retry_total"] == 0   # SDK 재시도와 겹치지 않게 꺼 둔다
    get_settings.cache_clear()


def test_429_wait_longer_than_allowance_stops_immediately(patch_client, monkeypatch):
    """서버가 허용치보다 긴 대기를 요구하면 기다리지 않고 PROVIDER_RATE_LIMITED로 끝낸다."""
    err = HttpResponseError(message="throttled")
    err.status_code = 429
    err.response = type("R", (), {"status_code": 429, "headers": {"Retry-After": "600"}})()
    monkeypatch.setenv("COST_AZURE_MAX_RETRY_WAIT_SECONDS", "30")
    get_settings.cache_clear()
    slept = []
    patch_client(error=err)

    r = AzureCostProvider(sleeper=slept.append).fetch(SECRET, SUB, START, END)

    assert r.partial is True and r.error_code == "PROVIDER_RATE_LIMITED"
    assert slept == [] and r.api_calls == 1
    get_settings.cache_clear()


@pytest.mark.parametrize("header_value", ["-5", "", "next-tuesday", None])
def test_429_with_unusable_retry_header_does_not_retry(patch_client, header_value):
    """음수·빈 값·해석 불가는 '안내 없음'이다 — 0으로 만들어 즉시 재시도하지 않는다."""
    err = HttpResponseError(message="throttled")
    err.status_code = 429
    headers = {} if header_value is None else {"Retry-After": header_value}
    err.response = type("R", (), {"status_code": 429, "headers": headers})()
    slept = []
    patch_client(error=err)

    r = AzureCostProvider(sleeper=slept.append).fetch(SECRET, SUB, START, END)

    assert r.partial is True and r.error_code == "PROVIDER_RATE_LIMITED"
    assert slept == [] and r.api_calls == 1


def test_retry_after_http_date_is_parsed():
    """ARM 문서는 초 단위를 쓰지만 HTTP 날짜 형식도 방어적으로 받는다."""
    from email.utils import format_datetime

    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=8)
    parsed = az.parse_retry_after(format_datetime(future))
    assert parsed is not None and 5 <= parsed <= 10
    assert az.parse_retry_after("-1") is None and az.parse_retry_after("쓰레기") is None


def test_timeout_is_provider_api_error(patch_client):
    patch_client(error=ServiceResponseError(message="timeout"))

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_mid_page_failure_reports_partial_with_rows_so_far(patch_client):
    err = HttpResponseError(message="boom")
    err.status_code = 500
    patch_client(
        pages=[_page([[1.0, 20260901, "USD", "VM", "Usage"]],
                     next_link="https://management.azure.com/next?$skiptoken=AAA")],
        follow_error=err,
    )

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"
    assert len(r.rows) == 1        # 호출부가 저장하지 않는다(08 §4-4) — 어댑터는 사실만 전한다


# --- 잘못된 응답은 조용히 버리지 않는다 ----------------------------------------------------------


@pytest.mark.parametrize("columns", [
    ["UsageDate", "Currency", "ServiceName"],                 # 금액 열 없음
    ["PreTaxCost", "Currency", "ServiceName"],                # 일자 열 없음
    ["PreTaxCost", "UsageDate", "ServiceName"],               # 통화 열 없음
])
def test_missing_required_column_fails(patch_client, columns):
    patch_client(pages=[_page([[1.0, 20260901, "USD", "VM"][:len(columns)]], columns=columns)])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_unparsable_amount_fails(patch_client):
    patch_client(pages=[_page([["not-a-number", 20260901, "USD", "VM", "Usage"]])])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_unparsable_date_fails(patch_client):
    patch_client(pages=[_page([[1.0, "어제", "USD", "VM", "Usage"]])])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


# --- 날짜 경계·분류 -------------------------------------------------------------------------------


def test_rows_outside_requested_range_are_dropped(patch_client):
    """`to`의 포함/제외가 불확실해 제외 경계를 그대로 보내고 받은 뒤 [start, end)로 다시 거른다."""
    patch_client(pages=[_page([
        [1.0, 20260831, "USD", "VM", "Usage"],    # 범위 앞
        [2.0, 20260901, "USD", "VM", "Usage"],
        [3.0, 20260904, "USD", "VM", "Usage"],    # end(제외) 당일
    ])])

    r = _fetch()

    assert [x.period_start for x in r.rows] == [dt.date(2026, 9, 1)]


def test_unknown_charge_type_becomes_other(patch_client):
    patch_client(pages=[_page([[1.0, 20260901, "USD", "VM", "SomethingNew"]])])

    r = _fetch()

    assert r.rows[0].charge_category == "other"       # usage로 넣지 않는다


def test_query_definition_uses_actual_cost_daily_and_two_dimensions(patch_client):
    holder = patch_client(pages=[_page([])])

    _fetch()

    params = holder["client"].query.calls[0]["parameters"]
    assert holder["client"].query.calls[0]["scope"] == f"/subscriptions/{SUB}"
    assert params.type == "ActualCost" and params.timeframe == "Custom"
    assert params.dataset.granularity == "Daily"
    assert [g.name for g in params.dataset.grouping] == ["ServiceName", "ChargeType"]
    assert params.time_period.from_property.date() == START
    assert params.time_period.to.date() == END        # 제외 경계를 그대로 보낸다(받은 뒤 다시 거른다)


# --- 원통화 보존: 통화를 추측하지 않는다 (2026-09-24 보완) ---------------------------------------


def test_missing_currency_on_first_row_fails(patch_client):
    """금액이 있는데 통화를 모르면 USD로 대체하지 않고 형식 오류로 실패한다."""
    patch_client(pages=[_page([[1.0, 20260901, "", "VM", "Usage"]])])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_missing_currency_on_later_row_does_not_inherit_previous(patch_client):
    """중간 행의 통화 누락도 이전 행 통화로 메우지 않는다 — 합계가 조용히 틀어진다."""
    patch_client(pages=[_page([
        [1.0, 20260901, "KRW", "VM", "Usage"],
        [2.0, 20260902, None, "VM", "Usage"],
    ])])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_empty_response_keeps_currency_none(patch_client):
    patch_client(pages=[_page([])])

    r = _fetch()

    assert r.partial is False and r.currency is None and r.rows == []


# --- 응답 구조·값 검증 ----------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_amount_is_rejected(patch_client, amount):
    patch_client(pages=[_page([[amount, 20260901, "USD", "VM", "Usage"]])])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_missing_group_column_is_format_error_not_other(patch_client):
    """ChargeType **열 자체**가 없으면 우리가 요청한 그룹이 빠진 것이다 — 값이 모르는 문자열인
    경우(other)와 다르게 취급한다."""
    patch_client(pages=[_page([[1.0, 20260901, "USD", "VM"]],
                              columns=["PreTaxCost", "UsageDate", "Currency", "ServiceName"])])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_unknown_charge_type_value_is_other_not_error(patch_client):
    """값이 모르는 문자열이면 other로 보낸다(열은 있으므로 형식 오류가 아니다)."""
    patch_client(pages=[_page([[1.0, 20260901, "USD", "VM", "BrandNewChargeType"]])])

    r = _fetch()

    assert r.partial is False and r.rows[0].charge_category == "other"


def test_short_row_is_rejected(patch_client):
    patch_client(pages=[_page([[1.0, 20260901]])])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_malformed_properties_are_not_coerced_to_empty(patch_client):
    """`rows`가 목록이 아니면 성공한 빈 응답으로 보정하지 않는다."""
    bad = {"columns": [{"name": "PreTaxCost", "type": "Number"}], "rows": {"oops": 1}}
    patch_client(pages=[bad])

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_endless_429_stops_even_when_sends_are_not_counted(monkeypatch):
    """429가 끝없이 와도 **유한 번**만 시도한다.

    2026-09-25: 요청 집계를 transport로 옮기면서 재시도 루프의 종료 조건까지 거기에만 의존하게 됐고,
    전송이 세어지지 않는 경로(스텁·집계 없는 transport)에서 무한 루프가 실제로 났다. 종료 조건은
    집계와 별개로 루프 자신이 갖는다."""
    err = HttpResponseError(message="throttled")
    err.status_code = 429
    err.response = type("R", (), {"status_code": 429, "headers": {"Retry-After": "0"}})()
    attempts = {"n": 0}

    class _AlwaysBusyQuery:
        def usage(self, scope, parameters):
            attempts["n"] += 1
            if attempts["n"] > 50:               # 무한 루프면 여기서 테스트가 끝난다(실패로)
                raise AssertionError("재시도가 멈추지 않았다")
            raise err

    class _Client:
        def __init__(self, **kwargs):
            self.query = _AlwaysBusyQuery()
            self._serialize = type("S", (), {"body": staticmethod(lambda obj, name: {})})()

        def close(self): pass

    monkeypatch.setattr(az, "ClientSecretCredential", lambda **kw: object())
    monkeypatch.setattr(az, "CostManagementClient", lambda credential, **kw: _Client(**kw))
    monkeypatch.setattr(az, "RequestsTransport", lambda *a, **kw: _NoopTransport())
    monkeypatch.setenv("COST_AZURE_MAX_REQUESTS", "4")
    get_settings.cache_clear()

    r = AzureCostProvider(sleeper=lambda s: None).fetch(SECRET, SUB, START, END)

    assert r.partial is True
    assert attempts["n"] <= 4                    # 상한 이상으로 두드리지 않는다
    get_settings.cache_clear()
