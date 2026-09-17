"""GCP 리소스 액션 확장 검증 (`gwonhyung/be-gcp-resource-actions`).

Compute Engine만 지원하던 시작/중지/삭제를 Cloud SQL(start/stop/delete)·Cloud Storage(delete)·
Cloud CDN(delete)까지 넓혔다 — AWS가 RDS/S3까지 지원하는 것과 격차가 있었다(실사용자 확인).
여기서는 순수 로직(허용 동작 판정, force_empty 전달)만 검증한다 — 실제 GCP REST 호출 자체는
discover_resources()와 마찬가지로 실 계정 검증으로 확인했다(단위 테스트에서 HTTP를 모킹하지 않는
기존 관례를 따름).
"""

from __future__ import annotations

import app.providers.gcp as gcp_provider
from app.resource_actions import ResourceActionError, perform_action, supported_actions


def test_gcp_cloud_sql_supports_full_lifecycle():
    assert supported_actions("gcp", "cloud_sql", "Cloud SQL Instance") == {"start", "stop", "delete"}


def test_gcp_cloud_storage_and_cdn_support_delete_only():
    assert supported_actions("gcp", "cloud_storage", "Cloud Storage Bucket") == {"delete"}
    assert supported_actions("gcp", "cloud_cdn", "Cloud CDN (HTTP LB)") == {"delete"}


def test_gcp_compute_engine_unaffected():
    assert supported_actions("gcp", "compute_engine", "Compute Engine Instance") == {"start", "stop", "delete"}


def test_unsupported_gcp_action_is_rejected_before_dispatch(monkeypatch):
    called = []
    monkeypatch.setattr(gcp_provider, "perform_resource_action", lambda *a, **k: called.append((a, k)))

    try:
        perform_action(
            provider="gcp", service_code="cloud_storage", original_resource_type="Cloud Storage Bucket",
            action="start", secret_payload={}, external_account_id="proj-1", region=None,
            external_resource_id="bucket-1",
        )
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "UNSUPPORTED_OPERATION"
    assert called == []  # 어댑터까지 도달하지 않고 사전에 막혀야 한다


def test_force_empty_is_threaded_to_gcp_provider(monkeypatch):
    captured = {}

    def _fake(service_code, action, secret_payload, external_account_id, region, external_resource_id, force_empty=False):
        captured["force_empty"] = force_empty
        captured["service_code"] = service_code

    monkeypatch.setattr(gcp_provider, "perform_resource_action", _fake)

    perform_action(
        provider="gcp", service_code="cloud_storage", original_resource_type="Cloud Storage Bucket",
        action="delete", secret_payload={}, external_account_id="proj-1", region=None,
        external_resource_id="bucket-1", force_empty=True,
    )

    assert captured == {"force_empty": True, "service_code": "cloud_storage"}
