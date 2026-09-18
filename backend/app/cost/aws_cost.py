"""AWS Cost Explorer 실측 수집 어댑터(PR 4). 호출은 `ce:GetCostAndUsage` 하나뿐이다
(확정 10 — 비용 최소화. `GetCostAndUsageWithResources`·`GetCostForecast`·CUR/Data Exports는
각각 리소스 단위 과금·유료 호출·S3 파싱 부담이라 쓰지 않는다).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.cost.base import CostFetchResult, CostRow
from app.logging_config import log_business_event

_TIMEOUT_CONFIG = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1})

# Cost Explorer는 글로벌 엔드포인트다 — 계정의 다른 리전으로 클라이언트를 만들면 실패한다.
_CE_REGION = "us-east-1"

# RECORD_TYPE -> charge_category. 모르는 RECORD_TYPE을 usage로 넣지 않는다 — usage는 예산
# 판정의 기준이라(확정 5) 정체 모를 값이 섞이면 소진율이 틀어진다. other로 보내고 원문을
# 로그에 남긴다(docs/비용_개발문서/08_백엔드_구현가이드.md §4-3).
_RECORD_TYPE_TO_CHARGE_CATEGORY = {
    "Usage": "usage",
    "DiscountedUsage": "usage",
    "SavingsPlanCoveredUsage": "usage",
    "Credit": "credit",
    "SavingsPlanNegation": "credit",
    "Refund": "refund",
    "Tax": "tax",
}


def _charge_category(record_type: str) -> str:
    category = _RECORD_TYPE_TO_CHARGE_CATEGORY.get(record_type)
    if category is None:
        log_business_event("cost.aws.unknown_record_type", record_type=record_type)
        return "other"
    return category


class AwsCostProvider:
    def fetch(
        self,
        secret_payload: dict,
        external_account_id: str,
        period_start: dt.date,
        period_end: dt.date,
    ) -> CostFetchResult:
        try:
            ce = boto3.client(
                "ce",
                aws_access_key_id=secret_payload.get("access_key_id"),
                aws_secret_access_key=secret_payload.get("secret_access_key"),
                aws_session_token=secret_payload.get("session_token") or None,
                region_name=_CE_REGION,
                config=_TIMEOUT_CONFIG,
            )
        except (BotoCoreError, KeyError):
            return CostFetchResult(
                rows=[], currency=None, covered_through=None, api_calls=0,
                partial=True, error_code="PROVIDER_AUTHENTICATION_FAILED",
            )

        rows: list[CostRow] = []
        currency: str | None = None
        api_calls = 0
        next_token: str | None = None

        while True:
            kwargs: dict = {
                "TimePeriod": {"Start": period_start.isoformat(), "End": period_end.isoformat()},
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost"],
                "GroupBy": [
                    {"Type": "DIMENSION", "Key": "SERVICE"},
                    {"Type": "DIMENSION", "Key": "RECORD_TYPE"},
                ],
            }
            if next_token:
                kwargs["NextPageToken"] = next_token

            try:
                response = ce.get_cost_and_usage(**kwargs)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "")
                if error_code in ("ThrottlingException", "RequestLimitExceeded"):
                    mapped = "PROVIDER_RATE_LIMITED"
                elif error_code in ("AccessDeniedException", "UnauthorizedOperation"):
                    mapped = "CLOUD_PERMISSION_DENIED"
                else:
                    mapped = "PROVIDER_API_ERROR"
                # 이미 모은 페이지가 있으면 부분 결과로 보고한다 — 중간에 멈췄다고 0건으로
                # 보고하면 "실제로 0원 사용"과 구분이 안 된다(확정 3 — 전체를 못 받으면 partial).
                return CostFetchResult(
                    rows=rows, currency=currency, covered_through=None,
                    api_calls=api_calls, partial=True, error_code=mapped,
                )
            except BotoCoreError:
                return CostFetchResult(
                    rows=rows, currency=currency, covered_through=None,
                    api_calls=api_calls, partial=True, error_code="PROVIDER_API_ERROR",
                )

            api_calls += 1

            for period_group in response.get("ResultsByTime", []):
                time_period = period_group.get("TimePeriod", {})
                try:
                    row_start = dt.date.fromisoformat(time_period["Start"])
                    row_end = dt.date.fromisoformat(time_period["End"])
                except (KeyError, ValueError):
                    continue

                for group in period_group.get("Groups", []):
                    keys = group.get("Keys", [])
                    service = keys[0] if len(keys) > 0 and keys[0] else None
                    record_type = keys[1] if len(keys) > 1 else "Usage"
                    metric = group.get("Metrics", {}).get("UnblendedCost", {})
                    amount_str = metric.get("Amount")
                    row_currency = metric.get("Unit")
                    if amount_str is None:
                        continue
                    try:
                        amount = Decimal(amount_str)
                    except InvalidOperation:
                        continue
                    if row_currency:
                        currency = row_currency

                    charge_category = _charge_category(record_type)
                    rows.append(
                        CostRow(
                            period_start=row_start,
                            period_end=row_end,
                            service=service,
                            charge_category=charge_category,
                            amount=amount,
                            currency=row_currency or "USD",
                            is_estimated=bool(period_group.get("Estimated", False)),
                            source_record_key=(
                                f"aws:{external_account_id}:{row_start.isoformat()}:"
                                f"{service or ''}:{record_type}"
                            ),
                        )
                    )

            next_token = response.get("NextPageToken")
            if not next_token:
                break

        return CostFetchResult(
            rows=rows, currency=currency, covered_through=period_end - dt.timedelta(days=1),
            api_calls=api_calls, partial=False,
        )
