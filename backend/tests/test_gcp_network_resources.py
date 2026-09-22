"""app/providers/gcp.py::list_network_resources() 단위 테스트(2026-09-22).

실제 GCP SDK를 부르지 않고 service_account.Credentials/compute_v1.NetworksClient/
AuthorizedSession을 monkeypatch한다. 핵심 검증: VPC 네트워크에 더해 Storage 버킷까지
같이 반환해서, CDN "기존 버킷 연결" 드롭다운(frontend EXISTING_RESOURCE_FIELDS.cdn.gcp)이
쓸 데이터를 채운다.
"""

from __future__ import annotations

import types

import app.providers.gcp as gcp

_SECRET = {"type": "service_account", "client_email": "x@p.iam.gserviceaccount.com"}


class _FakeNetworksClient:
    def __init__(self, credentials):
        pass

    def list(self, project):
        return iter([
            types.SimpleNamespace(name="default", self_link="https://.../networks/default", auto_create_subnetworks=True),
        ])


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, credentials):
        pass

    def get(self, url):
        assert "storage.googleapis.com" in url
        return _FakeResponse({"items": [
            {"name": "my-bucket-1", "location": "ASIA-NORTHEAST3"},
            {"name": "my-bucket-2", "location": "US-CENTRAL1"},
        ]})


def _patch(monkeypatch):
    fake_creds = types.SimpleNamespace(with_scopes=lambda scopes: fake_creds)
    monkeypatch.setattr(
        gcp.service_account.Credentials, "from_service_account_info", staticmethod(lambda payload: fake_creds)
    )
    monkeypatch.setattr(gcp.compute_v1, "NetworksClient", _FakeNetworksClient)
    monkeypatch.setattr(gcp, "AuthorizedSession", _FakeSession)


def test_returns_networks_and_buckets(monkeypatch):
    _patch(monkeypatch)

    result = gcp.list_network_resources(_SECRET, "proj-1")

    assert result["networks"] == [
        {"name": "default", "self_link": "https://.../networks/default", "auto_create_subnetworks": True}
    ]
    # location은 provisioning.py의 버킷 생성 저장 관례(소문자)와 맞춘다.
    assert result["buckets"] == [
        {"name": "my-bucket-1", "location": "asia-northeast3"},
        {"name": "my-bucket-2", "location": "us-central1"},
    ]


def test_raises_provider_api_error_on_auth_failure(monkeypatch):
    from app.resource_actions import ResourceActionError

    def _boom(payload):
        raise KeyError("client_email")

    monkeypatch.setattr(gcp.service_account.Credentials, "from_service_account_info", staticmethod(_boom))

    try:
        gcp.list_network_resources(_SECRET, "proj-1")
        assert False, "raise 됐어야 한다"
    except ResourceActionError as exc:
        assert exc.code == "PROVIDER_API_ERROR"
