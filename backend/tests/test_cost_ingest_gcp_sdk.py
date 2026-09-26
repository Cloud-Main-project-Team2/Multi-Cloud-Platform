"""GCP 어댑터를 **설치된 실제 BigQuery SDK 경로**로 검증한다 — 네트워크는 쓰지 않는다.

가짜 클라이언트를 만들지 않는다. 진짜 `bigquery.Client`가 진짜 job 직렬화·REST 호출 구성을
태우고, **자격증명과 HTTP 세션만** 대체한다. 그래서 다음이 실제로 검증된다:
요청 URL, job 설정(dryRun·파라미터 모드·maximum_bytes_billed), 우리가 만든 SQL이 그대로 실리는지.

가짜 클라이언트에만 있는 동작으로 통과하는 일이 없도록, 이 파일은 `gcp_cost` 모듈의
`bigquery.Client`를 **바꾸지 않는다**(자격증명과 `_http`만 바꾼다).
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery

import app.cost.gcp_cost as gcp
from app.config import get_settings
from app.cost.gcp_cost import GcpCostProvider

PROJECT = "demo-project"
TABLE = "billing-proj.billing_dataset.gcp_billing_export_v1_0123AB"
SECRET = {"type": "service_account", "project_id": PROJECT}
START, END = dt.date(2026, 9, 1), dt.date(2026, 9, 4)

JOB_REF = {"projectId": "billing-proj", "jobId": "job-1"}


class _Response:
    def __init__(self, payload, status=200, *, method="POST", url="https://bigquery.googleapis.com/"):
        self.status_code = status
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.text = json.dumps(payload)
        self.headers = {"Content-Type": "application/json"}
        # 오류 경로에서 google-api-core가 response.request.{method,url}을 읽는다(exceptions.py:555).
        self.request = type("R", (), {"method": method, "url": url})()
        self.reason = "OK" if status < 400 else "Error"

    def json(self):
        return self._payload


class _Session:
    """BigQuery REST를 흉내 내는 최소 세션. 보낸 요청을 그대로 기록한다."""

    is_mtls = False

    def __init__(self, *, dry_bytes=1000, rows=None, schema=None):
        self.dry_bytes = dry_bytes
        self.rows = rows or []
        self.schema = schema or []
        self.requests = []

    def request(self, method, url, data=None, headers=None, timeout=None, **kwargs):
        body = json.loads(data) if data else {}
        self.requests.append({"method": method, "url": url, "body": body})
        if "/queries/" in url:                       # getQueryResults
            return _Response({
                "jobReference": JOB_REF, "jobComplete": True,
                "schema": {"fields": self.schema}, "rows": self.rows, "totalRows": str(len(self.rows)),
            })
        dry = bool(body.get("configuration", {}).get("dryRun"))
        return _Response({
            "jobReference": JOB_REF, "configuration": body.get("configuration", {}),
            "status": {"state": "DONE"},
            "statistics": {"query": {"totalBytesProcessed": str(self.dry_bytes)}} if dry else {},
        })


def _schema():
    return [
        {"name": "usage_date", "type": "DATE", "mode": "NULLABLE"},
        {"name": "service_description", "type": "STRING", "mode": "NULLABLE"},
        {"name": "cost_type", "type": "STRING", "mode": "NULLABLE"},
        {"name": "currency", "type": "STRING", "mode": "NULLABLE"},
        {"name": "cost", "type": "FLOAT", "mode": "NULLABLE"},
        {"name": "credit_amount", "type": "FLOAT", "mode": "NULLABLE"},
    ]


def _row(day="2026-09-02", service="Compute Engine", cost_type="regular", currency="KRW",
         cost="1500.0", credit="0"):
    return {"f": [{"v": day}, {"v": service}, {"v": cost_type}, {"v": currency}, {"v": cost}, {"v": credit}]}


@pytest.fixture()
def sdk(monkeypatch):
    """실제 bigquery.Client를 쓰되 자격증명과 HTTP 세션만 대체한다."""
    holder = {}

    def _install(session, **env):
        monkeypatch.setenv("COST_GCP_EXPORT_TABLES", f"{PROJECT}:{TABLE}")
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        get_settings.cache_clear()
        monkeypatch.setattr(gcp.service_account.Credentials, "from_service_account_info",
                            staticmethod(lambda info, scopes=None: AnonymousCredentials()))
        real_client = gcp.bigquery.Client
        holder["session"] = session
        monkeypatch.setattr(gcp.bigquery, "Client",
                            lambda project=None, credentials=None: real_client(
                                project=project, credentials=credentials, _http=session))
        return holder

    yield _install
    get_settings.cache_clear()


def _fetch():
    return GcpCostProvider().fetch(SECRET, PROJECT, START, END)


def test_real_request_url_and_job_configuration(sdk):
    h = sdk(_Session(schema=_schema(), rows=[_row()]))

    r = _fetch()

    reqs = h["session"].requests
    assert reqs[0]["method"] == "POST"
    assert reqs[0]["url"].startswith("https://bigquery.googleapis.com/bigquery/v2/projects/billing-proj/jobs")
    dry_cfg = reqs[0]["body"]["configuration"]
    assert dry_cfg["dryRun"] is True                       # 첫 요청은 반드시 dry-run이다
    assert dry_cfg["query"]["useQueryCache"] is False
    assert dry_cfg["query"]["parameterMode"] == "NAMED"
    assert f"`{TABLE}`" in dry_cfg["query"]["query"]
    params = {p["name"]: p["parameterValue"]["value"] for p in dry_cfg["query"]["queryParameters"]}
    assert params["period_start"].startswith("2026-09-01") and params["period_end"].startswith("2026-09-04")
    assert all(p["parameterType"]["type"] == "TIMESTAMP" for p in dry_cfg["query"]["queryParameters"])
    assert r.partial is False and [str(x.amount) for x in r.rows] == ["1500.000000"]


def test_real_second_request_carries_the_scan_limit(sdk):
    h = sdk(_Session(schema=_schema(), rows=[_row()]), COST_GCP_MAX_SCANNED_BYTES=777777)

    _fetch()

    real_cfg = h["session"].requests[1]["body"]["configuration"]
    assert "dryRun" not in real_cfg or real_cfg["dryRun"] is False
    assert real_cfg["query"]["maximumBytesBilled"] == "777777"     # 서버에도 상한을 건다
    assert real_cfg["query"]["query"] == h["session"].requests[0]["body"]["configuration"]["query"]["query"]


def test_real_dry_run_over_limit_sends_only_one_request(sdk):
    h = sdk(_Session(dry_bytes=9_000_000, schema=_schema()), COST_GCP_MAX_SCANNED_BYTES=1_000)

    r = _fetch()

    assert r.partial is True and r.error_code == "PROVIDER_API_ERROR"
    assert len(h["session"].requests) == 1 and r.api_calls == 1


def test_real_rows_are_parsed_through_the_sdk(sdk):
    h = sdk(_Session(schema=_schema(), rows=[
        _row(day="2026-09-02", cost="1000.5", credit="-200.25"),
        _row(day="2026-09-03", service="Cloud Storage", cost_type="tax", cost="10", credit="0"),
    ]))

    r = _fetch()

    assert [(x.period_start.isoformat(), x.charge_category, str(x.amount), x.currency) for x in r.rows] == [
        ("2026-09-02", "usage", "1000.500000", "KRW"),
        ("2026-09-02", "credit", "-200.250000", "KRW"),
        ("2026-09-03", "tax", "10.000000", "KRW"),
    ]
    assert r.api_calls == 2 and len(h["session"].requests) >= 2


def test_real_permission_error_maps_to_cloud_permission_denied(sdk):
    class _Denied(_Session):
        def request(self, method, url, data=None, headers=None, timeout=None, **kwargs):
            self.requests.append({"method": method, "url": url, "body": {}})
            return _Response({"error": {"code": 403, "message": "Access Denied",
                                        "errors": [{"reason": "accessDenied"}]}}, status=403)

    sdk(_Denied())

    r = _fetch()

    assert r.partial is True and r.error_code == "CLOUD_PERMISSION_DENIED"


def test_real_missing_table_maps_to_setup_required(sdk):
    class _Missing(_Session):
        def request(self, method, url, data=None, headers=None, timeout=None, **kwargs):
            self.requests.append({"method": method, "url": url, "body": {}})
            return _Response({"error": {"code": 404, "message": "Not found: Table",
                                        "errors": [{"reason": "notFound"}]}}, status=404)

    sdk(_Missing())

    r = _fetch()

    assert r.partial is True and r.error_code == "COST_SETUP_REQUIRED"
