"""GCP 실측 비용 수집 어댑터 — **BigQuery 청구 Export 테이블**을 읽는다(2026-09-26).

AWS·Azure와 모양이 다르다. GCP에는 "비용 조회 API"가 없고, 결제 계정이 BigQuery로 내보낸
**Export 테이블**이 사실상의 원천이다. 그래서 위험도 다르다 — 요청 횟수가 아니라 **스캔한
바이트(=과금)**다. 실행 전에 dry-run으로 스캔량을 재고, 상한을 넘으면 실제 쿼리를 보내지 않는다.

## 확인한 사실 (공식 문서, 2026-09-26)

| 항목 | 값 | 근거 |
|---|---|---|
| 테이블 이름 | `gcp_billing_export_v1_<BILLING_ACCOUNT_ID>` | https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage |
| 시각 열 | `usage_start_time`·`usage_end_time`(**시간 단위** Timestamp), `export_time` | 같은 문서 |
| 금액·통화 | `cost`(Float), `currency`(String), `currency_conversion_rate` | 같은 문서 |
| 서비스/SKU | `service.id`·`service.description`, `sku.id`·`sku.description` | 같은 문서 |
| 크레딧 | `credits` **배열**, 원소에 `amount`·`type`·`name` | 같은 문서 |
| 요금 종류 | `cost_type` = regular / tax / adjustment / rounding_error | 같은 문서 |
| 지연 도착 | "월말에 늦게 보고된 사용량은 그 달 청구서에 없을 수 있다" — **나중에 늘어난다** | 같은 문서 |

## 남은 불확실성 (실제 테이블이 있어야 확정)

- **파티션 열**: 표준 export가 `_PARTITIONTIME` 기준인지 문서가 명시하지 않는다. 없는 열을
  참조하면 쿼리가 실패하므로 **`usage_start_time`으로만 거른다** — 대신 스캔량이 커질 수 있어
  dry-run 상한으로 막는다. 실제 테이블에서 파티션을 확인하면 그때 프루닝을 추가한다.
- **상세(detailed) export**는 열이 더 많지만(리소스 단위) 표준 export의 열을 포함한다 —
  이 쿼리는 표준 열만 쓰므로 둘 다에서 동작할 것으로 보이나 **미확인**이다.
- 통화가 계정 안에서 섞일 수 있는지(재판매/파트너) 미확인 — 섞여 와도 행별 통화를 그대로 보존한다.

## 계약상 고정

- `coverage_basis="observed_only"` — Export는 **나중에 늘어난다**(지연 도착). "이 날은 다 왔다"고
  말할 근거가 없으므로 판정(전망·예산 소진율·급증)에 쓰지 않는다. 금액은 그대로 보여 준다.
- `covered_through=None`, `is_estimated=True`.
- 원통화 보존: 금액이 있는데 통화가 비면 **추측하지 않고 형식 오류로 실패**한다.
- 중간 실패·상한 초과는 `partial=True` — 호출부가 저장하지 않는다(부분 응답으로 기간을 갈아치우지
  않는다).

## api_calls

BigQuery **작업(job) 수**를 센다 — dry-run 1 + 실제 쿼리 1 = 2가 정상이다. dry-run은 과금되지
않지만 "몇 번 불렀나"를 숨기지 않기 위해 함께 센다. 스캔 바이트는 별도 축이라 로그로 남긴다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from google.api_core.exceptions import (
    BadRequest,
    Forbidden,
    GoogleAPICallError,
    NotFound,
    Unauthorized,
)
from google.auth.exceptions import GoogleAuthError
from google.cloud import bigquery
from google.oauth2 import service_account

from app.config import get_settings
from app.cost.base import CostFetchResult, CostRow
# 설정 해석은 google import가 없는 조각에 있다 — capability(= CSP를 부르지 않는 조회)도 같은 규칙을 쓴다.
from app.cost.gcp_export_config import (  # noqa: F401  (테스트·호출부가 이 이름으로 쓴다)
    export_table_for as _configured_export_table,
    is_valid_table_ref,
    parse_export_tables,
)
from app.logging_config import log_business_event

BIGQUERY_SCOPES = ("https://www.googleapis.com/auth/bigquery",)

# cost_type -> charge_category. 모르는 값을 usage로 넣지 않는다 — usage는 예산·급증 판정의
# 기준이라 정체 모를 값이 섞이면 소진율이 틀어진다(AWS·Azure 어댑터와 같은 정책).
_COST_TYPE_TO_CATEGORY = {
    "regular": "usage",
    "tax": "tax",
    "adjustment": "other",
    "rounding_error": "other",
}



class GcpExportNotConfigured(Exception):
    """이 계정의 Export 테이블이 설정되지 않았다 — 추측해서 아무 테이블이나 읽지 않는다."""


class GcpScanBudgetExceeded(Exception):
    """예상 스캔량이 상한을 넘었다 — 실제 쿼리를 보내지 않는다(과금 방지)."""


class GcpResponseFormatError(Exception):
    """결과가 우리가 요청한 형식과 다르다 — 조용히 0으로 만들지 않는다."""


def export_table_for(external_account_id: str) -> str:
    table = _configured_export_table(external_account_id)
    if not table:
        raise GcpExportNotConfigured(external_account_id)
    return table


def build_query(table: str) -> str:
    """일자·서비스·요금종류·통화로 묶어 한 번에 집계한다. 기간은 쿼리 파라미터로 넘긴다 —
    테이블 이름만 문자열로 들어가며, 그 값은 위에서 형식 검사를 통과한 것이다."""
    return f"""
SELECT
  DATE(usage_start_time, 'UTC') AS usage_date,
  service.description AS service_description,
  cost_type,
  currency,
  SUM(cost) AS cost,
  SUM((SELECT IFNULL(SUM(c.amount), 0) FROM UNNEST(credits) AS c)) AS credit_amount
FROM `{table}`
WHERE usage_start_time >= @period_start
  AND usage_start_time < @period_end
GROUP BY usage_date, service_description, cost_type, currency
ORDER BY usage_date
""".strip()


def _decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise GcpResponseFormatError("금액을 십진수로 읽을 수 없습니다") from exc
    if not amount.is_finite():
        raise GcpResponseFormatError("금액이 유한한 수가 아닙니다")
    return amount.quantize(Decimal("0.000001"))


def _charge_category(cost_type) -> str:
    key = str(cost_type or "").strip().lower()
    category = _COST_TYPE_TO_CATEGORY.get(key)
    if category is None:
        log_business_event("cost.gcp.unknown_cost_type", cost_type=str(cost_type)[:40])
        return "other"
    return category


def _usage_date(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise GcpResponseFormatError("usage_date를 날짜로 읽을 수 없습니다") from exc


def _field(row, name: str):
    """BigQuery Row는 매핑처럼 접근된다. 없는 열은 형식 오류다(0으로 보정하지 않는다)."""
    try:
        return row[name]
    except (KeyError, IndexError) as exc:
        raise GcpResponseFormatError(f"결과에 없는 열: {name}") from exc


class GcpCostProvider:
    """`CostProvider` 프로토콜 구현. 프로젝트 하나 = 우리 cloud_account 하나."""

    def fetch(
        self,
        secret_payload: dict,
        external_account_id: str,
        period_start: dt.date,
        period_end: dt.date,        # 제외 경계(우리 계약)
    ) -> CostFetchResult:
        settings = get_settings()
        max_bytes = max(int(settings.cost_gcp_max_scanned_bytes), 0)
        timeout = max(int(settings.cost_gcp_query_timeout_seconds), 1)
        state = {"api_calls": 0}

        def fail(code: str) -> CostFetchResult:
            return CostFetchResult(
                rows=[], currency=None, covered_through=None,
                api_calls=state["api_calls"], partial=True, error_code=code,
                coverage_basis="observed_only",
            )

        try:
            table = export_table_for(external_account_id)
        except GcpExportNotConfigured:
            log_business_event("cost.gcp.export_not_configured", level="WARNING")
            return fail("COST_SETUP_REQUIRED")

        try:
            credentials = service_account.Credentials.from_service_account_info(
                secret_payload, scopes=list(BIGQUERY_SCOPES)
            )
        except (KeyError, ValueError, TypeError, GoogleAuthError):
            return fail("PROVIDER_AUTHENTICATION_FAILED")

        # 쿼리를 실행하는 프로젝트는 **테이블이 있는 프로젝트**로 둔다(청구도 그쪽으로 간다).
        query_project = table.split(".")[0]
        client = bigquery.Client(project=query_project, credentials=credentials)
        query = build_query(table)
        params = [
            bigquery.ScalarQueryParameter(
                "period_start", "TIMESTAMP", dt.datetime.combine(period_start, dt.time.min, tzinfo=dt.timezone.utc)
            ),
            bigquery.ScalarQueryParameter(
                "period_end", "TIMESTAMP", dt.datetime.combine(period_end, dt.time.min, tzinfo=dt.timezone.utc)
            ),
        ]

        try:
            # 1) dry-run — 얼마나 스캔할지 **먼저 재고** 상한을 넘으면 실제 쿼리를 보내지 않는다.
            state["api_calls"] += 1
            dry = client.query(query, job_config=bigquery.QueryJobConfig(
                dry_run=True, use_query_cache=False, query_parameters=params))
            estimated = int(getattr(dry, "total_bytes_processed", 0) or 0)
            log_business_event("cost.gcp.dry_run", estimated_bytes=estimated, max_bytes=max_bytes)
            if estimated > max_bytes:
                raise GcpScanBudgetExceeded(f"{estimated} > {max_bytes}")

            # 2) 실제 쿼리 — 상한을 서버에도 건다(예상이 빗나가도 그 이상 과금되지 않는다).
            state["api_calls"] += 1
            job = client.query(query, job_config=bigquery.QueryJobConfig(
                query_parameters=params, maximum_bytes_billed=max_bytes, use_query_cache=True))
            rows, currency = self._rows_from_result(
                job.result(timeout=timeout), external_account_id, period_start, period_end
            )
        except GcpScanBudgetExceeded as exc:
            log_business_event("cost.gcp.scan_budget_exceeded", level="WARNING", reason=str(exc)[:80])
            return fail("PROVIDER_API_ERROR")
        except GcpResponseFormatError as exc:
            log_business_event("cost.gcp.response_format_error", level="ERROR", reason=str(exc)[:120])
            return fail("PROVIDER_API_ERROR")
        except (Forbidden, Unauthorized) as exc:
            log_business_event("cost.gcp.permission_denied", level="WARNING",
                               exception=type(exc).__name__, http_status=getattr(exc, "code", None))
            return fail("CLOUD_PERMISSION_DENIED")
        except NotFound:
            # 테이블이 없다 = Export가 아직 안 켜졌거나 이름이 틀렸다. 권한 문제와 섞지 않는다.
            log_business_event("cost.gcp.export_table_not_found", level="WARNING")
            return fail("COST_SETUP_REQUIRED")
        except (BadRequest, GoogleAPICallError, GoogleAuthError) as exc:
            log_business_event("cost.gcp.api_error", level="WARNING", exception=type(exc).__name__)
            return fail("PROVIDER_API_ERROR")
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
    @staticmethod
    def _rows_from_result(result, external_account_id, period_start, period_end):
        out: list[CostRow] = []
        currency: str | None = None

        for raw in result:
            day = _usage_date(_field(raw, "usage_date"))
            if not (period_start <= day < period_end):
                continue                      # 시간대 경계로 넘어온 행은 우리 범위로 다시 거른다
            cost = _decimal(_field(raw, "cost"))
            credit = _decimal(_field(raw, "credit_amount"))
            row_currency = str(_field(raw, "currency") or "").strip()
            if not row_currency and (cost != 0 or credit != 0):
                # 금액이 있는데 통화를 모른다 → 추측하지 않는다(원통화 보존).
                raise GcpResponseFormatError("행에 통화가 없습니다")
            if not row_currency:
                continue                      # 금액도 통화도 없는 행은 담을 것이 없다
            currency = row_currency

            service = _field(raw, "service_description")
            service = str(service).strip() if service not in (None, "") else None
            category = _charge_category(_field(raw, "cost_type"))
            key_base = f"gcp:{external_account_id}:{day.isoformat()}:{service or ''}"

            if cost != 0:
                out.append(CostRow(
                    period_start=day, period_end=day + dt.timedelta(days=1), service=service,
                    charge_category=category, amount=cost, currency=row_currency, is_estimated=True,
                    source_record_key=f"{key_base}:{category}",
                ))
            if credit != 0:
                # 크레딧은 별도 행이다 — usage에 섞으면 예산 소진율이 실제보다 작아 보인다.
                out.append(CostRow(
                    period_start=day, period_end=day + dt.timedelta(days=1), service=service,
                    charge_category="credit", amount=credit, currency=row_currency, is_estimated=True,
                    source_record_key=f"{key_base}:credit",
                ))
        return out, currency
