"""Azure 리소스 액션 확장 검증 (2026-09-18).

VM만 지원하던 시작/중지/삭제에 Storage Account/SQL Database/CDN의 삭제를 추가했다 — 동기화
탐색 범위가 이 세 타입까지 넓어지면서(app/providers/azure.py:discover_resources()) 인벤토리에
보이기 시작했는데, 삭제 버튼을 누르면 UNSUPPORTED_OPERATION만 뜨는 걸 실사용 중 발견했다.

이 세 타입의 external_resource_id는 VM과 달리 ARM 전체 ID가 아니라 짧은 이름이라(프로비저닝과
동기화가 키를 맞추려고 그렇게 저장한다), perform_resource_action이 구독 전체 리소스를 나열해
이름·타입이 일치하는 항목을 찾은 뒤 그 항목의 진짜 ARM ID로 삭제한다 — 그 탐색 로직을
ResourceManagementClient를 모킹해 검증한다(discover_resources 단위 테스트와 같은 방식).
"""

from __future__ import annotations

import types

import app.providers.azure as azure_provider
from app.resource_actions import ResourceActionError, perform_action, supported_actions

_SECRET = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}


def test_azure_storage_sql_cdn_support_delete_only():
    assert supported_actions("azure", "storage_account", "Microsoft.Storage/storageAccounts") == {"delete"}
    assert supported_actions("azure", "sql_database", "Azure Database for MySQL") == {"delete"}
    assert supported_actions("azure", "cdn", "Microsoft.Cdn/profiles") == {"delete"}


def test_azure_vm_unaffected():
    assert supported_actions("azure", "vm", "Virtual Machine") == {"start", "stop", "delete"}


def test_unsupported_azure_action_is_rejected_before_dispatch(monkeypatch):
    called = []
    monkeypatch.setattr(azure_provider, "perform_resource_action", lambda *a, **k: called.append((a, k)))

    try:
        perform_action(
            provider="azure", service_code="storage_account", original_resource_type="Microsoft.Storage/storageAccounts",
            action="start", secret_payload={}, external_account_id="sub-1", region=None,
            external_resource_id="mcptestbucket12353",
        )
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "UNSUPPORTED_OPERATION"
    assert called == []  # 어댑터까지 도달하지 않고 사전에 막혀야 한다


def test_external_account_id_threaded_to_azure_provider(monkeypatch):
    captured = {}

    def _fake(service_code, action, secret_payload, external_resource_id, *, external_account_id=None):
        captured["service_code"] = service_code
        captured["external_account_id"] = external_account_id

    monkeypatch.setattr(azure_provider, "perform_resource_action", _fake)

    perform_action(
        provider="azure", service_code="storage_account", original_resource_type="Microsoft.Storage/storageAccounts",
        action="delete", secret_payload={}, external_account_id="sub-1", region=None,
        external_resource_id="mcptestbucket12353",
    )

    assert captured == {"service_code": "storage_account", "external_account_id": "sub-1"}


def _obj(**kwargs):
    return types.SimpleNamespace(**kwargs)


class _FakeDeleteOp:
    def __init__(self, sink: dict):
        self._sink = sink

    def result(self):
        return None


class _FakeResourceMgmt:
    def __init__(self, resources):
        self._resources = resources
        self.deleted: list[tuple[str, str]] = []

        def _begin_delete_by_id(resource_id, api_version):
            self.deleted.append((resource_id, api_version))
            return _FakeDeleteOp(self.deleted)

        self.resources = types.SimpleNamespace(list=lambda: iter(self._resources), begin_delete_by_id=_begin_delete_by_id)


def _patch(monkeypatch, resources) -> _FakeResourceMgmt:
    fake = _FakeResourceMgmt(resources)
    monkeypatch.setattr(azure_provider, "ClientSecretCredential", lambda **kw: object())
    monkeypatch.setattr(azure_provider, "ResourceManagementClient", lambda cred, sub: fake)
    return fake


def test_delete_storage_account_finds_by_name_and_deletes(monkeypatch):
    resources = [
        _obj(
            id="/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Storage/storageAccounts/mcptestbucket12353",
            name="mcptestbucket12353", type="Microsoft.Storage/storageAccounts",
        ),
        _obj(
            id="/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Network/virtualNetworks/other-vnet",
            name="mcptestbucket12353", type="Microsoft.Network/virtualNetworks",  # 이름은 같지만 삭제 대상 타입이 아님
        ),
    ]
    fake = _patch(monkeypatch, resources)

    azure_provider.perform_resource_action(
        "storage_account", "delete", _SECRET, "mcptestbucket12353", external_account_id="sub-1",
    )

    assert fake.deleted == [
        (
            "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Storage/storageAccounts/mcptestbucket12353",
            "2023-01-01",
        )
    ]


def test_delete_sql_database_matches_any_of_three_engine_types(monkeypatch):
    resources = [
        _obj(
            id="/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.DBforPostgreSQL/flexibleServers/mcp-pg-9",
            name="mcp-pg-9", type="Microsoft.DBforPostgreSQL/flexibleServers",
        ),
    ]
    fake = _patch(monkeypatch, resources)

    azure_provider.perform_resource_action("sql_database", "delete", _SECRET, "mcp-pg-9", external_account_id="sub-1")

    assert fake.deleted == [
        (
            "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.DBforPostgreSQL/flexibleServers/mcp-pg-9",
            "2023-06-01-preview",
        )
    ]


def test_delete_raises_provider_api_error_when_resource_not_found(monkeypatch):
    _patch(monkeypatch, resources=[])

    try:
        azure_provider.perform_resource_action(
            "storage_account", "delete", _SECRET, "does-not-exist", external_account_id="sub-1",
        )
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "PROVIDER_API_ERROR"


def test_delete_raises_provider_api_error_without_account_id(monkeypatch):
    _patch(monkeypatch, resources=[])

    try:
        azure_provider.perform_resource_action("storage_account", "delete", _SECRET, "mcptestbucket12353")
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "PROVIDER_API_ERROR"
