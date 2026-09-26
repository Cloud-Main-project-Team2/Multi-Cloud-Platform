"""Azure Cost Management 실측 수집 어댑터(B단계, 2026-09-23 / 보완 2026-09-24).

**읽어서 표준형으로 돌려주기만 한다** — DB·스케줄러·트랜잭션을 모른다(`cost/base.py`).

## 확인한 사실 (설치 SDK + 공식 문서)

| 항목 | 값 | 근거 |
|---|---|---|
| SDK / API 버전 | `azure-mgmt-costmanagement==4.0.1`, 기본 `api_version="2022-10-01"` | `_configuration.py:37` |
| 쿼리 타입 | `Usage` / `ActualCost` / `AmortizedCost` | `models/_cost_management_client_enums.py::ExportType` |
| timeframe / granularity | `Custom` / `Daily` | 같은 파일 `TimeframeType`·`GranularityType` |
| 그룹화 | `QueryGrouping(type="Dimension" 또는 "TagKey", name=...)`, **최대 2개** | `models/_models_py3.py::QueryDataset` docstring |
| 응답 | `properties.columns[{name,type}]` + `rows[[...]]` **위치 배열** | https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage |
| 일자 열 | `UsageDate`가 **Number `20180331`**(yyyyMMdd) | 같은 문서 샘플 |
| 금액 열 | 샘플은 `PreTaxCost` + `Currency` | 같은 문서 샘플 |
| 페이지 | `properties.nextLink`. **SDK 자동 페이징 없음**(단일 `QueryResult` 반환) | `operations/_query_operations.py:122` |
| 빈 응답 | `204 No Content` → SDK가 `None` 반환 | 같은 파일 279-287 |
| 오류 | 401만 `ClientAuthenticationError`, **429는 일반 `HttpResponseError`** | 같은 파일 232-237 |
| 한도 | 테넌트당 **12 QPU/10초 · 60/분 · 600/시간**(1개월 조회 = 1 QPU), 429 시 `x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after`(초) | https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/manage-automation |
| `Retry-After` | ARM 429는 "**the number of seconds** your application should wait" | https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/request-limits-and-throttling |
| ChargeType / ServiceName | 둘 다 존재. 환불·구매는 **ActualCost에서만** 보인다 | https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/group-filter |
| SDK 기본 재시도 | `retry_total=10`이 429·5xx 자동 재시도 → **끄고 우리가 제한한다** | `azure/core/pipeline/policies/_retry.py:78-82` |
| 리다이렉트 | `RedirectPolicy(permit_redirects=...)`가 kwargs로 설정된다. 301·302는 GET/HEAD만, **303·307·308·300은 POST도 따라간다** → 우리 요청(POST)은 307 계열에서 추가 전송이 생길 수 있어 **끈다** | `azure/core/pipeline/policies/_redirect.py:81·126-134`, `costmanagement/_configuration.py:56` |

## 남은 불확실성 (실제 응답이 있어야 확정)

- `timePeriod.to`의 포함/제외가 문서에 없다 → **제외 경계를 그대로 보내고** 받은 행을 `[start, end)`로
  다시 거른다. 포함이면 하루가 더 와서 걸러지고, 제외면 정확히 우리 범위다.
- 금액 열 이름이 계약 유형(EA/MCA/PAYG)에 따라 다를 수 있다 → 집계 별칭·`PreTaxCost`·`Cost`를
  **이름으로** 찾고, 없으면 **응답 형식 오류로 실패**(0으로 만들지 않는다).
- `ChargeType`의 실제 문자열 값 목록 → 모르는 **값**은 `other`로 보내고 로그에 남긴다. 단
  **열 자체가 없으면** 우리가 요청한 그룹이 빠진 것이므로 **형식 오류**다(값 누락과 다르게 취급).
- 세금·크레딧이 이 경로로 오는지 **미확인** — 안 온다고 0으로 만들지 않는다.

## 계약상 고정

- `coverage_basis="observed_only"` / `covered_through=None` — 정상 빈 응답도 승격하지 않는다(A-2).
- 원통화 보존: **행의 통화를 추측하지 않는다.** 금액이 있는데 통화를 모르면 형식 오류로 실패한다
  (이전 행 통화나 USD로 대체하지 않는다). 행이 없으면 `currency=None`.
- `is_estimated=True` — 확정 신호가 없어 확정이라 주장하지 않는다. ⚠️ bool 계약에서는 "잠정"과
  "확정 여부 미확인"을 구분할 수 없어 잠정 배지가 계속 붙는다. `coverage_basis`와는 다른 축이다.
- 중간 실패·상한 초과는 `partial=True` — 호출부가 **저장하지 않는다**(08 §4-4).

## api_calls의 범위

**비용 조회로 실제 나간 HTTP 요청 수**다. 우리 루프가 아니라 transport(`_BudgetedTransport`)가
세므로, 우리가 모르는 경로로 나가는 요청도 포함된다 — 그래서 `api_calls`는 추정이 아니라 실측이고
상한도 같은 지점에서 강제된다(보내기 **전에** 검사).

- SDK 자동 재시도: `retry_total=0`으로 껐다.
- 리다이렉트: `permit_redirects=False`로 껐다 — 3xx는 따라가지 않고 오류로 올린다(빈 응답으로
  처리하지 않는다). 따라서 리다이렉트로 요청 수가 늘어나는 경로가 없다.
- 인증 challenge 재전송(`ARMChallengeAuthenticationPolicy`)처럼 SDK 내부에서 같은 요청을 다시
  보내는 경로도 transport를 지나므로 세어지고, 상한에 걸리면 전송되지 않는다.
- **AAD 토큰 발급 요청은 별도다** — credential이 자기 파이프라인·자기 transport로 보내므로 이
  숫자에 들어가지 않는다(비용 API 호출만 센다는 정의 그대로).

QPU 소모량은 이 값과 다른 축이라 여기서 보장하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import time
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.core.pipeline.transport import RequestsTransport
from azure.core.rest import HttpRequest
from azure.identity import ClientSecretCredential
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryAggregation,
    QueryDataset,
    QueryDefinition,
    QueryGrouping,
    QueryTimePeriod,
)

from app.config import get_settings
from app.cost.base import CostFetchResult, CostRow
from app.logging_config import log_business_event

ARM_HOST = "management.azure.com"
AMOUNT_ALIAS = "totalCost"
AMOUNT_COLUMN_CANDIDATES = (AMOUNT_ALIAS, "PreTaxCost", "Cost")
DATE_COLUMN_CANDIDATES = ("UsageDate", "Date")
CURRENCY_COLUMN_CANDIDATES = ("Currency", "CurrencyCode")
# 429 back-off 안내 헤더 — Cost Management 전용 헤더가 우선이고, 없으면 ARM의 Retry-After(초).
RETRY_AFTER_HEADERS = ("x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after", "Retry-After")
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 60

# ChargeType 값 -> charge_category. 모르는 값을 usage로 넣지 않는다 — usage는 예산·급증 판정의
# 기준이라 정체 모를 값이 섞이면 소진율이 틀어진다(AWS 어댑터와 같은 정책).
# ⚠️ Purchase(예약·저축 플랜 구매)는 "사용료"가 아니라서 other다. credit·tax가 이 경로로 오는지는
# 확인되지 않았다 — 오면 매핑을 늘리고, 안 오면 없는 것을 만들지 않는다.
_CHARGE_TYPE_TO_CATEGORY = {
    "usage": "usage",
    "refund": "refund",
    "purchase": "other",
    "unusedreservation": "other",
    "unusedsavingsplan": "other",
}


class AzureResponseFormatError(Exception):
    """응답이 우리가 요청한 형식과 다르다 — 조용히 버리거나 []로 보정하지 않고 실패로 올린다."""


class AzureRequestBudgetExceeded(Exception):
    """승인된 요청 상한을 넘기려 한다 — 남은 페이지가 있으므로 성공으로 끝내지 않는다."""


class _BudgetedTransport:
    """비용 API로 **실제 나가는 HTTP 요청**을 세고, 상한을 넘으면 보내기 전에 막는다.

    우리 페이지 루프만 세면 SDK 내부에서 나가는 요청(리다이렉트 추적·인증 challenge 재전송·
    SDK 재시도)이 빠진다. 파이프라인의 가장 바깥이 아니라 **가장 안쪽**인 transport에서 세면
    경로와 무관하게 실제 전송 횟수가 곧 `api_calls`다.

    AAD 토큰 발급은 credential이 **자기 파이프라인·자기 transport**로 보내므로 여기를 지나지
    않는다 — 의도한 대로다(`api_calls`는 비용 조회 요청만 센다)."""

    def __init__(self, inner, state: dict, max_requests: int) -> None:
        self._inner = inner
        self._state = state
        self._max = max_requests

    def send(self, request, **kwargs):
        if self._state["api_calls"] >= self._max:
            raise AzureRequestBudgetExceeded(f"request limit {self._max}")
        self._state["api_calls"] += 1
        return self._inner.send(request, **kwargs)

    # 나머지는 transport 규약 그대로 위임한다
    def open(self): return self._inner.open()
    def close(self): return self._inner.close()
    def sleep(self, duration): return self._inner.sleep(duration)
    def __enter__(self): self._inner.__enter__(); return self
    def __exit__(self, *args): return self._inner.__exit__(*args)


def _charge_category(charge_type) -> str:
    key = str(charge_type or "").strip().replace(" ", "").lower()
    category = _CHARGE_TYPE_TO_CATEGORY.get(key)
    if category is None:
        log_business_event("cost.azure.unknown_charge_type", charge_type=str(charge_type)[:40])
        return "other"
    return category


def _parse_usage_date(value) -> dt.date:
    """`20260901`(Number) · `"20260901"` · `"2026-09-01T00:00:00"` 모두 받는다."""
    if isinstance(value, bool) or value is None:
        raise AzureResponseFormatError("UsageDate 값이 없습니다")
    if isinstance(value, (int, float)):
        value = str(int(value))
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        try:
            return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError as exc:
            raise AzureResponseFormatError("UsageDate가 날짜가 아닙니다") from exc
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError as exc:
        raise AzureResponseFormatError("UsageDate 형식을 해석할 수 없습니다") from exc


def _parse_amount(value) -> Decimal:
    """금액은 유한한 십진수여야 한다 — NaN·Infinity는 합계를 오염시키므로 거부한다."""
    if value is None or isinstance(value, bool):
        raise AzureResponseFormatError("금액이 없습니다")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AzureResponseFormatError("금액을 해석할 수 없습니다") from exc
    if not amount.is_finite():
        raise AzureResponseFormatError("금액이 유한하지 않습니다(NaN/Infinity)")
    return amount


def _column_index(columns: list[str], *names: str) -> int | None:
    lowered = [str(c or "").lower() for c in columns]
    for name in names:
        if name.lower() in lowered:
            return lowered.index(name.lower())
    return None


def parse_retry_after(raw) -> float | None:
    """`Retry-After`/QPU 헤더 → 초. ARM은 초 단위를 쓰지만(공식 문서) HTTP 날짜 형식도 방어적으로
    받는다. 음수·해석 불가는 `None`(안내 없음)으로 본다 — 0으로 만들어 즉시 재시도하지 않는다."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        seconds = (when - dt.datetime.now(dt.timezone.utc)).total_seconds()
    if seconds < 0:
        return None
    return seconds


def _retry_after_from(exc: HttpResponseError) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    for key in RETRY_AFTER_HEADERS:
        for candidate in (key, key.lower(), key.title()):
            if candidate in headers:
                parsed = parse_retry_after(headers[candidate])
                if parsed is not None:
                    return parsed
    return None


def _status_of(exc: HttpResponseError) -> int | None:
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)


class AzureCostProvider:
    """`CostProvider` 프로토콜 구현. 구독 하나 = 우리 cloud_account 하나."""

    def __init__(self, *, sleeper=time.sleep) -> None:
        # 테스트가 실제로 잠들지 않도록 주입 가능하게 둔다(운영 기본값은 time.sleep).
        self._sleep = sleeper

    def fetch(
        self,
        secret_payload: dict,
        external_account_id: str,
        period_start: dt.date,
        period_end: dt.date,        # 제외 경계(우리 계약)
    ) -> CostFetchResult:
        settings = get_settings()
        max_pages = max(int(settings.cost_azure_max_pages), 1)
        max_requests = max(int(settings.cost_azure_max_requests), 1)
        max_wait = max(int(settings.cost_azure_max_retry_wait_seconds), 0)

        state = {"api_calls": 0}

        def fail(code: str, rows=None, currency=None) -> CostFetchResult:
            return CostFetchResult(
                rows=rows or [], currency=currency, covered_through=None,
                api_calls=state["api_calls"], partial=True, error_code=code,
                coverage_basis="observed_only",
            )

        try:
            credential = ClientSecretCredential(
                tenant_id=secret_payload["tenant_id"],
                client_id=secret_payload["client_id"],
                client_secret=secret_payload["client_secret"],
            )
            client = CostManagementClient(
                credential,
                # 실제 전송을 우리가 세는 transport로 감싼다 — 상한은 여기서 강제된다.
                transport=_BudgetedTransport(RequestsTransport(), state, max_requests),
                retry_total=0,                       # SDK 재시도와 우리 재시도가 겹치지 않게
                permit_redirects=False,              # 3xx를 따라가며 추가 요청을 만들지 않는다
                connection_timeout=CONNECT_TIMEOUT_SECONDS,
                read_timeout=READ_TIMEOUT_SECONDS,
            )
        except (KeyError, ValueError, TypeError, ClientAuthenticationError):
            return fail("PROVIDER_AUTHENTICATION_FAILED")

        scope = f"/subscriptions/{external_account_id}"
        definition = _build_definition(period_start, period_end)

        rows: list[CostRow] = []
        currency: str | None = None
        next_link: str | None = None
        seen_links: set[str] = set()
        pages = 0

        try:
            while True:
                pages += 1
                if pages > max_pages:
                    # 남은 페이지가 있는데 상한에 걸렸다 — 성공으로 끝내지 않는다.
                    raise AzureRequestBudgetExceeded("page limit")

                payload = self._request_page(
                    client, scope, definition, next_link, state, max_requests, max_wait
                )
                if payload is None:          # 204 No Content — 데이터 없음(0원이라는 뜻은 아니다)
                    break

                page_rows, page_currency = _rows_from_payload(
                    payload, external_account_id, period_start, period_end
                )
                rows.extend(page_rows)
                currency = page_currency or currency

                next_link = payload.get("nextLink")
                if not next_link:
                    break
                if next_link in seen_links:      # 같은 링크 반복 = 끝나지 않는다
                    raise AzureResponseFormatError("nextLink가 반복됩니다")
                seen_links.add(next_link)

        except ClientAuthenticationError:
            return fail("PROVIDER_AUTHENTICATION_FAILED", rows, currency)
        except HttpResponseError as exc:
            status = _status_of(exc)
            code = ("CLOUD_PERMISSION_DENIED" if status == 403
                    else "PROVIDER_RATE_LIMITED" if status == 429
                    else "PROVIDER_API_ERROR")
            return fail(code, rows, currency)
        except (ServiceRequestError, ServiceResponseError):        # 연결 실패·타임아웃
            return fail("PROVIDER_API_ERROR", rows, currency)
        except AzureRequestBudgetExceeded:
            log_business_event("cost.azure.request_budget_exceeded", level="WARNING",
                               pages=pages, api_calls=state["api_calls"],
                               max_pages=max_pages, max_requests=max_requests)
            return fail("PROVIDER_API_ERROR", rows, currency)
        except AzureResponseFormatError as exc:
            log_business_event("cost.azure.response_format_error", level="ERROR", reason=str(exc)[:120])
            return fail("PROVIDER_API_ERROR", rows, currency)
        finally:
            try:
                client.close()
            except Exception:       # noqa: BLE001 — 닫기 실패가 수집 결과를 바꾸지 않는다
                pass

        return CostFetchResult(
            rows=rows, currency=currency, covered_through=None,
            api_calls=state["api_calls"], partial=False, coverage_basis="observed_only",
        )

    # ── 내부 ────────────────────────────────────────────────────────────────────────
    def _request_page(self, client, scope, definition, next_link, state, max_requests, max_wait):
        """페이지 하나를 받는다. 429면 **서버가 요구한 시간만큼** 기다렸다 다시 시도하되,
        요구 시간이 허용치를 넘거나 요청 상한에 걸리면 기다리지 않고 올린다."""
        attempts = 0
        while True:
            # 세는 곳은 transport 한 곳이다(_BudgetedTransport) — 여기서 또 세면 어긋난다.
            # 다만 **루프의 종료 조건까지** 거기에만 맡기지는 않는다: 전송이 세어지지 않는 경로(스텁 등)
            # 에서 429가 계속 오면 영원히 돌 수 있다. 시도 횟수로도 같은 상한을 건다.
            attempts += 1
            if attempts > max_requests:
                raise AzureRequestBudgetExceeded("retry attempts")
            try:
                if next_link is None:
                    return _query_result_to_dict(client.query.usage(scope=scope, parameters=definition))
                return _follow_next_link(client, next_link, definition)
            except HttpResponseError as exc:
                if _status_of(exc) != 429:
                    raise
                wait = _retry_after_from(exc)
                if wait is None:
                    raise                      # 안내가 없으면 임의로 더 두드리지 않는다
                if wait > max_wait:
                    raise                      # 허용 대기시간을 넘으면 PROVIDER_RATE_LIMITED로 끝낸다
                if state["api_calls"] >= max_requests:
                    raise AzureRequestBudgetExceeded("request limit before retry")
                self._sleep(wait)              # 서버가 요구한 시간보다 **일찍** 재시도하지 않는다


def _build_definition(period_start: dt.date, period_end: dt.date) -> QueryDefinition:
    return QueryDefinition(
        type="ActualCost",
        timeframe="Custom",
        time_period=QueryTimePeriod(
            from_property=dt.datetime.combine(period_start, dt.time.min, tzinfo=dt.timezone.utc),
            to=dt.datetime.combine(period_end, dt.time.min, tzinfo=dt.timezone.utc),
        ),
        dataset=QueryDataset(
            granularity="Daily",
            aggregation={AMOUNT_ALIAS: QueryAggregation(name="PreTaxCost", function="Sum")},
            grouping=[                       # 그룹은 최대 2개다
                QueryGrouping(type="Dimension", name="ServiceName"),
                QueryGrouping(type="Dimension", name="ChargeType"),
            ],
        ),
    )


def _follow_next_link(client: CostManagementClient, next_link: str, definition: QueryDefinition) -> dict | None:
    """nextLink를 따라간다. **토큰을 붙여 보내기 전에 대상 호스트를 검증한다** — 응답이 준 URL을
    그대로 믿고 자격증명을 실어 보내지 않는다."""
    parsed = urlparse(next_link)
    if parsed.scheme != "https" or parsed.hostname != ARM_HOST:
        raise AzureResponseFormatError("nextLink 호스트가 예상과 다릅니다")
    body = client._serialize.body(definition, "QueryDefinition")     # noqa: SLF001 — 공개 API가 없다
    response = client._send_request(HttpRequest("POST", next_link, json=body))   # noqa: SLF001
    if response.status_code == 204:
        return None
    if response.status_code == 401:
        # 첫 페이지는 SDK의 error_map이 401을 ClientAuthenticationError로 바꾼다. 후속 페이지는
        # 우리가 직접 보내므로 같은 매핑을 여기서 한다 — 안 하면 인증 만료가 일반 API 오류로 보인다.
        raise ClientAuthenticationError(response=response)
    if response.status_code != 200:
        # 3xx도 포함한다 — 리다이렉트를 껐으므로 3xx는 "본문 없는 정상"이 아니라 오류다.
        raise HttpResponseError(response=response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise AzureResponseFormatError("응답이 JSON이 아닙니다") from exc
    return _properties_to_dict(payload)


def _properties_to_dict(payload) -> dict:
    """`{"properties": {...}}` → 우리 내부 dict. **없거나 형식이 다르면 []로 보정하지 않는다.**"""
    if not isinstance(payload, dict):
        raise AzureResponseFormatError("응답이 객체가 아닙니다")
    props = payload.get("properties")
    if not isinstance(props, dict):
        raise AzureResponseFormatError("properties가 없습니다")
    columns, rows = props.get("columns"), props.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise AzureResponseFormatError("columns/rows가 목록이 아닙니다")
    return {"columns": columns, "rows": rows, "nextLink": props.get("nextLink")}


def _query_result_to_dict(result) -> dict | None:
    """SDK 모델(또는 204의 None)을 내부 dict로. 모델이 비정상이면 빈 결과로 보정하지 않는다."""
    if result is None:
        return None
    columns, rows = getattr(result, "columns", None), getattr(result, "rows", None)
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise AzureResponseFormatError("columns/rows가 목록이 아닙니다")
    return {
        "columns": [{"name": getattr(c, "name", None), "type": getattr(c, "type", None)} for c in columns],
        "rows": rows,
        "nextLink": getattr(result, "next_link", None),
    }


def _rows_from_payload(
    payload: dict, external_account_id: str, period_start: dt.date, period_end: dt.date
) -> tuple[list[CostRow], str | None]:
    raw_columns = payload.get("columns") or []
    columns = [c.get("name") if isinstance(c, dict) else getattr(c, "name", None) for c in raw_columns]
    raw_rows = payload.get("rows") or []

    # 열은 **이름으로** 찾는다 — 위치는 API 버전·계약에 따라 달라진다.
    i_amount = _column_index(columns, *AMOUNT_COLUMN_CANDIDATES)
    i_date = _column_index(columns, *DATE_COLUMN_CANDIDATES)
    i_currency = _column_index(columns, *CURRENCY_COLUMN_CANDIDATES)
    # 우리가 명시적으로 요청한 그룹 열이다 — 없으면 응답이 요청과 다르다는 뜻이라 형식 오류다.
    # (열은 있는데 **값**이 모르는 문자열인 것과는 다른 사건이다 → 그건 other로 보낸다.)
    i_service = _column_index(columns, "ServiceName")
    i_charge = _column_index(columns, "ChargeType")

    if not raw_rows:
        # 행이 0건인 정상 응답. 열 구성까지 트집 잡지 않는다(204와 같은 뜻으로 본다).
        return [], None
    missing = [name for name, idx in (
        ("금액", i_amount), ("일자", i_date), ("통화", i_currency),
        ("ServiceName", i_service), ("ChargeType", i_charge),
    ) if idx is None]
    if missing:
        raise AzureResponseFormatError(f"응답에 없는 열: {', '.join(missing)}")

    out: list[CostRow] = []
    currency: str | None = None
    need = max(i_amount, i_date, i_currency, i_service, i_charge)
    for row in raw_rows:
        if not isinstance(row, (list, tuple)) or len(row) <= need:
            raise AzureResponseFormatError("행 길이가 열 수와 맞지 않습니다")
        day = _parse_usage_date(row[i_date])
        if not (period_start <= day < period_end):
            continue                     # `to` 포함/제외가 불확실해 우리 범위로 다시 거른다
        amount = _parse_amount(row[i_amount])       # 음수(환불) 보존 · NaN/Inf 거부
        row_currency = str(row[i_currency] or "").strip()
        if not row_currency:
            # 금액이 있는데 통화를 모른다 → 추측하지 않는다. 이전 행 통화나 USD로 대체하면
            # 원통화 보존(ADR-004)이 깨지고 합계가 조용히 틀어진다.
            raise AzureResponseFormatError("행에 통화가 없습니다")
        currency = row_currency
        service = row[i_service]
        service = str(service).strip() if service not in (None, "") else None
        charge_type = row[i_charge]
        out.append(
            CostRow(
                period_start=day,
                period_end=day + dt.timedelta(days=1),
                service=service,
                charge_category=_charge_category(charge_type),
                amount=amount,
                currency=row_currency,
                is_estimated=True,          # 확정 신호가 없다 — 확정이라 주장하지 않는다
                source_record_key=(
                    f"azure:{external_account_id}:{day.isoformat()}:"
                    f"{service or ''}:{str(charge_type or '')}"
                ),
            )
        )
    return out, currency
