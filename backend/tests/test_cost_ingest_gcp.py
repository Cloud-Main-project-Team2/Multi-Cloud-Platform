"""GCP 비용 수집 어댑터(app/cost/gcp_cost.py) — **실제 GCP를 절대 호출하지 않는다.**

BigQuery 클라이언트를 스텁으로 갈아 끼우고 결과 행 모양만 흉내 낸다. 확인하는 것:
Export 테이블 설정 해석·쿼리/파라미터 모양·스캔 상한(dry-run 게이트)·오류 매핑·크레딧 분리·
원통화 보존·날짜 경계·coverage_basis 고정.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from google.api_core.exceptions import BadRequest, Forbidden, NotFound, Unauthorized

import app.cost.gcp_cost as gcp
from app.config import get_settings
from app.cost.gcp_cost import GcpCostProvider

PROJECT = "demo-project"
TABLE = "billing-proj.billing_dataset.gcp_billing_export_v1_0123AB_CDEF45_6789GH"
SECRET = {
    "type": "service_account", "project_id": PROJECT, "client_email": "svc@demo.iam.gserviceaccount.com",
    "private_key_id": "kid", "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    "token_uri": "https://oauth2.googleapis.com/token",
}
START = dt.date(2026, 9, 1)
END = dt.date(2026, 9, 4)          # 제외 경계


def _row(day="2026-09-01", service="Compute Engine", cost_type="regular", currency="KRW",
         cost=1000.0, credit=0.0):
    return {"usage_date": dt.date.fromisoformat(day), "service_description": service,
            "cost_type": cost_type, "currency": currency, "cost": cost, "credit_amount": credit}


class _FakeJob:
    def __init__(self, rows=None, bytes_processed=0, result_error=None):
        self._rows = rows or []
        self.total_bytes_processed = bytes_processed
        self._result_error = result_error

    def result(self, timeout=None):
        if self._result_error is not None:
            raise self._result_error
        return list(self._rows)


class _FakeClient:
    """`client.query()` 두 번(dry-run → 실제)을 흉내 낸다. 보낸 job_config를 기록해 검증한다."""

    def __init__(self, *, rows=None, dry_bytes=1000, dry_error=None, query_error=None, result_error=None):
        self.rows = rows if rows is not None else [_row()]
        self.dry_bytes = dry_bytes
        self.dry_error = dry_error
        self.query_error = query_error
        self.result_error = result_error
        self.calls = []          # [(query, job_config)]
        self.closed = False

    def query(self, query, job_config=None):
        self.calls.append((query, job_config))
        if job_config is not None and getattr(job_config, "dry_run", False):
            if self.dry_error is not None:
                raise self.dry_error
            return _FakeJob(bytes_processed=self.dry_bytes)
        if self.query_error is not None:
            raise self.query_error
        return _FakeJob(rows=self.rows, result_error=self.result_error)

    def close(self):
        self.closed = True


@pytest.fixture()
def patch_bq(monkeypatch):
    holder = {}

    def _install(client=None, *, tables=f"{PROJECT}:{TABLE}", credential_error=None, **env):
        monkeypatch.setenv("COST_GCP_EXPORT_TABLES", tables)
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        get_settings.cache_clear()

        if credential_error is not None:
            def _raise(*a, **kw):
                raise credential_error
            monkeypatch.setattr(gcp.service_account.Credentials, "from_service_account_info", staticmethod(_raise))
        else:
            monkeypatch.setattr(gcp.service_account.Credentials, "from_service_account_info",
                                staticmethod(lambda info, scopes=None: object()))

        holder["client"] = client or _FakeClient()
        monkeypatch.setattr(gcp.bigquery, "Client", lambda project=None, credentials=None: holder["client"])
        holder["project_seen"] = None
        return holder

    yield _install
    get_settings.cache_clear()


def _fetch():
    return GcpCostProvider().fetch(SECRET, PROJECT, START, END)


# --- 정상 ---------------------------------------------------------------------------------------


def test_success_maps_rows_and_keeps_original_currency(patch_bq):
    h = patch_bq(_FakeClient(rows=[
        _row(day="2026-09-01", cost=1000.0),
        _row(day="2026-09-02", service="Cloud Storage", cost=250.5),
    ]))

    r = _fetch()

    assert r.partial is False and r.error_code is None
    assert [(x.period_start.isoformat(), x.service, x.charge_category, str(x.amount), x.currency) for x in r.rows] == [
        ("2026-09-01", "Compute Engine", "usage", "1000.000000", "KRW"),
        ("2026-09-02", "Cloud Storage", "usage", "250.500000", "KRW"),
    ]
    assert r.currency == "KRW"                      # 환산하지 않는다
    assert r.api_calls == 2                          # dry-run + 실제 쿼리
    assert h["client"].closed is True


def test_contract_fixed_observed_only(patch_bq):
    """Export는 나중에 늘어난다(지연 도착) — 요청 범위를 "확인"이라 부르지 않는다."""
    patch_bq(_FakeClient())

    r = _fetch()

    assert r.coverage_basis == "observed_only" and r.covered_through is None
    assert all(x.is_estimated for x in r.rows)


def test_credits_become_separate_rows(patch_bq):
    """크레딧을 usage에 섞으면 예산 소진율이 실제보다 작아 보인다 — 별도 행으로 뺀다."""
    patch_bq(_FakeClient(rows=[_row(cost=1000.0, credit=-300.0)]))

    r = _fetch()

    assert [(x.charge_category, str(x.amount)) for x in r.rows] == [
        ("usage", "1000.000000"), ("credit", "-300.000000")]
    assert len({x.source_record_key for x in r.rows}) == 2       # 키가 겹치지 않는다


@pytest.mark.parametrize("cost_type,expected", [
    ("regular", "usage"), ("tax", "tax"), ("adjustment", "other"),
    ("rounding_error", "other"), ("something_new", "other"),
])
def test_cost_type_mapping_never_invents_usage(patch_bq, cost_type, expected):
    patch_bq(_FakeClient(rows=[_row(cost_type=cost_type)]))

    r = _fetch()

    assert [x.charge_category for x in r.rows] == [expected]


def test_rows_outside_requested_range_are_dropped(patch_bq):
    patch_bq(_FakeClient(rows=[_row(day="2026-08-31"), _row(day="2026-09-02"), _row(day="2026-09-04")]))

    r = _fetch()

    assert [x.period_start.isoformat() for x in r.rows] == ["2026-09-02"]   # [start, end)


def test_empty_result_is_not_an_error(patch_bq):
    patch_bq(_FakeClient(rows=[]))

    r = _fetch()

    assert r.partial is False and r.rows == [] and r.currency is None


# --- 쿼리·파라미터 모양 --------------------------------------------------------------------------


def test_query_shape_and_parameters(patch_bq):
    h = patch_bq(_FakeClient())

    _fetch()

    dry_query, dry_config = h["client"].calls[0]
    real_query, real_config = h["client"].calls[1]
    assert dry_config.dry_run is True and dry_config.use_query_cache is False
    assert real_query == dry_query                       # 같은 쿼리를 재본다
    assert f"`{TABLE}`" in real_query
    assert "usage_start_time >= @period_start" in real_query and "usage_start_time < @period_end" in real_query
    assert "DATE(usage_start_time, 'UTC')" in real_query and "UNNEST(credits)" in real_query
    values = {p.name: p.value for p in real_config.query_parameters}
    assert values["period_start"] == dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    assert values["period_end"] == dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc)   # 제외 경계 그대로


def test_scan_limit_is_also_sent_to_the_server(patch_bq):
    h = patch_bq(_FakeClient(), COST_GCP_MAX_SCANNED_BYTES=12345)

    _fetch()

    _, real_config = h["client"].calls[1]
    assert real_config.maximum_bytes_billed == 12345     # 예상이 빗나가도 그 이상 과금되지 않는다


# --- 과금 방지 ----------------------------------------------------------------------------------


def test_dry_run_over_limit_does_not_send_the_real_query(patch_bq):
    h = patch_bq(_FakeClient(dry_bytes=5_000_000), COST_GCP_MAX_SCANNED_BYTES=1_000_000)

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"
    assert len(h["client"].calls) == 1 and r.api_calls == 1      # 실제 쿼리는 나가지 않았다
    assert r.rows == []


def test_dry_run_exactly_at_limit_proceeds(patch_bq):
    h = patch_bq(_FakeClient(dry_bytes=1000), COST_GCP_MAX_SCANNED_BYTES=1000)

    r = _fetch()

    assert r.partial is False and len(h["client"].calls) == 2


# --- 설정·오류 ----------------------------------------------------------------------------------


def test_export_table_not_configured_is_setup_required(patch_bq):
    h = patch_bq(_FakeClient(), tables="")               # 이 계정의 테이블이 없다

    r = _fetch()

    assert r.partial is True and r.error_code == "COST_SETUP_REQUIRED"
    assert h["client"].calls == []                        # 아무 쿼리도 보내지 않는다
    assert r.api_calls == 0


def test_other_accounts_table_is_not_borrowed(patch_bq):
    """다른 계정 설정을 끌어다 쓰지 않는다 — 남의 청구 데이터를 읽는 사고를 막는다."""
    h = patch_bq(_FakeClient(), tables=f"other-project:{TABLE}")

    r = _fetch()

    assert r.error_code == "COST_SETUP_REQUIRED" and h["client"].calls == []


@pytest.mark.parametrize("bad", [
    "proj:dataset.table",            # 세 토막이 아니다
    "proj:a.b.c.d",
    "proj:a.b.tab;DROP",             # 식별자에 쓸 수 없는 문자
    "proj:`a`.b.c",
])
def test_malformed_table_reference_is_rejected(patch_bq, bad):
    h = patch_bq(_FakeClient(), tables=bad.replace("proj", PROJECT))

    r = _fetch()

    assert r.error_code == "COST_SETUP_REQUIRED" and h["client"].calls == []


@pytest.mark.parametrize("error,expected", [
    (Forbidden("no access"), "CLOUD_PERMISSION_DENIED"),
    (Unauthorized("bad token"), "CLOUD_PERMISSION_DENIED"),
    (NotFound("no table"), "COST_SETUP_REQUIRED"),
    (BadRequest("bad sql"), "PROVIDER_API_ERROR"),
])
def test_error_mapping(patch_bq, error, expected):
    patch_bq(_FakeClient(dry_error=error))

    r = _fetch()

    assert r.partial is True and r.error_code == expected


def test_failure_during_result_fetch_is_partial(patch_bq):
    patch_bq(_FakeClient(result_error=BadRequest("job failed")))

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR" and r.rows == []


def test_bad_service_account_payload_is_authentication_failed(patch_bq):
    patch_bq(_FakeClient(), credential_error=ValueError("malformed key"))

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_AUTHENTICATION_FAILED"


# --- 값 검증 ------------------------------------------------------------------------------------


def test_amount_without_currency_is_a_format_error(patch_bq):
    """통화를 추측하지 않는다 — 원통화 보존이 깨지면 합계가 조용히 틀어진다."""
    patch_bq(_FakeClient(rows=[_row(currency="", cost=10.0)]))

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_zero_row_without_currency_is_skipped_not_fatal(patch_bq):
    patch_bq(_FakeClient(rows=[_row(currency="", cost=0.0, credit=0.0), _row(cost=5.0)]))

    r = _fetch()

    assert r.partial is False and [str(x.amount) for x in r.rows] == ["5.000000"]


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_amounts_are_rejected(patch_bq, value):
    patch_bq(_FakeClient(rows=[_row(cost=value)]))

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_missing_column_is_a_format_error(patch_bq):
    broken = {"usage_date": dt.date(2026, 9, 1), "cost": 1.0, "currency": "KRW"}   # service/cost_type 없음
    patch_bq(_FakeClient(rows=[broken]))

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"


def test_null_service_is_account_level_not_empty_string(patch_bq):
    patch_bq(_FakeClient(rows=[_row(service=None)]))

    r = _fetch()

    assert r.rows[0].service is None


def test_decimal_precision_is_six_places(patch_bq):
    patch_bq(_FakeClient(rows=[_row(cost=0.1234565)]))

    r = _fetch()

    assert str(r.rows[0].amount) == "0.123456" or str(r.rows[0].amount) == "0.123457"


# --- 설정 파싱 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("a:p.d.t", {"a": "p.d.t"}),
    (" a:p.d.t , b:p2.d2.t2 ", {"a": "p.d.t", "b": "p2.d2.t2"}),
    ("a:p.d.t,깨진항목", {"a": "p.d.t"}),
    ("a:not-three-parts", {}),
    ("", {}),
])
def test_export_table_parsing(raw, expected):
    assert gcp.parse_export_tables(raw) == expected
