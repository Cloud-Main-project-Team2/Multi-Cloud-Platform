"""app/providers/azure.py의 discover_resources 단위 테스트(2026-09-18).

실제 Azure SDK를 부르지 않고 ComputeManagementClient/ResourceManagementClient/
ClientSecretCredential를 monkeypatch한다. 핵심 검증: VM에 더해 Storage Account·SQL Database
(MySQL/PostgreSQL/SQL Server)까지 조회하고, service_code·external_resource_id가 프로비저닝이
저장하는 값(routers/provisioning.py의 `_resource_attrs`)과 맞아떨어져 동기화가 방금 만든 리소스를
같은 행으로 인식(= is_stale 해제)하도록 한다.
"""

from __future__ import annotations

import types

import app.providers.azure as azure

_SECRET = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}


def _obj(**kwargs):
    return types.SimpleNamespace(**kwargs)


class _FakeCompute:
    def __init__(self, vms):
        self.virtual_machines = types.SimpleNamespace(list_all=lambda: iter(vms))


class _FakeResourceMgmt:
    def __init__(self, resources):
        self.resources = types.SimpleNamespace(list=lambda: iter(resources))


def _patch(monkeypatch, vms, resources):
    monkeypatch.setattr(azure, "ClientSecretCredential", lambda **kw: object())
    monkeypatch.setattr(azure, "ComputeManagementClient", lambda cred, sub: _FakeCompute(vms))
    monkeypatch.setattr(azure, "ResourceManagementClient", lambda cred, sub: _FakeResourceMgmt(resources))


def test_discovers_vm_storage_and_sql(monkeypatch):
    vms = [
        _obj(
            id="/subscriptions/x/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
            name="vm1", location="eastus", provisioning_state="Succeeded", tags={"env": "dev"},
            # 비용 확장(PR #100)이 정가 추정을 위해 VM 사양을 읽는다(app/providers/azure.py) —
            # 실제 SDK 객체는 항상 이 필드를 갖고 있다.
            hardware_profile=_obj(vm_size="Standard_B2s"),
        )
    ]
    resources = [
        _obj(name="mcptestmcps322", type="Microsoft.Storage/storageAccounts", location="eastus", tags=None),
        _obj(name="mcp-db-8", type="Microsoft.DBforMySQL/flexibleServers", location="koreacentral", tags=None),
        _obj(name="mcp-pg-9", type="Microsoft.DBforPostgreSQL/flexibleServers", location="eastus", tags=None),
        _obj(name="mcp-mssql-10", type="Microsoft.Sql/servers", location="eastus", tags=None),
        # CDN은 최상위 Front Door profile로 잡는다(하위 route/endpoint는 resources.list()에 안 나옴).
        _obj(name="mcp-cdn01-11-fd-profile", type="Microsoft.Cdn/profiles", location="global", tags=None),
        # 관심 없는 타입(디스크·VNet 등)은 걸러야 한다 — VM은 compute 클라이언트로 이미 잡으므로
        # 제네릭 목록에서 다시 담으면 안 된다. CDN 하위 endpoint/route도 걸러야 한다.
        _obj(name="vm1_disk", type="Microsoft.Compute/disks", location="eastus", tags=None),
        _obj(name="vm1", type="Microsoft.Compute/virtualMachines", location="eastus", tags=None),
        _obj(name="ep1", type="Microsoft.Cdn/profiles/afdEndpoints", location="global", tags=None),
    ]
    _patch(monkeypatch, vms, resources)

    result = azure.discover_resources(_SECRET, "sub-1")
    by_key = {(r.service_code, r.external_resource_id): r for r in result}

    # VM은 ARM 전체 ID를 external_resource_id로 쓴다(resource_actions.py가 그 형식을 전제).
    assert ("vm", vms[0].id) in by_key
    # 비용 추정용 spec — "Standard_" 접두사는 어댑터가 제거한다(pricing.py 정가표 키 규칙).
    assert by_key[("vm", vms[0].id)].spec == {"instance_type": "B2s", "region": "eastus"}
    # Storage/SQL/CDN은 짧은 이름 — 프로비저닝 `_resource_attrs`와 동일.
    assert ("storage_account", "mcptestmcps322") in by_key
    assert ("sql_database", "mcp-db-8") in by_key
    assert ("sql_database", "mcp-pg-9") in by_key
    assert ("sql_database", "mcp-mssql-10") in by_key
    assert ("cdn", "mcp-cdn01-11-fd-profile") in by_key

    storage = by_key[("storage_account", "mcptestmcps322")]
    assert storage.original_resource_type == "Microsoft.Storage/storageAccounts"
    assert storage.region == "eastus"
    assert storage.status == "AVAILABLE"

    assert by_key[("sql_database", "mcp-db-8")].original_resource_type == "Azure Database for MySQL"
    assert by_key[("sql_database", "mcp-pg-9")].original_resource_type == "Azure Database for PostgreSQL"
    assert by_key[("sql_database", "mcp-mssql-10")].original_resource_type == "Azure SQL Database"

    # 2026-09-22 수정: SQL Database도 VM처럼 비용 추정용 spec(engine/region)을 채운다 — 이전엔
    # 빈 dict라 동기화로 발견한 Azure DB는 항상 "정가 추정 불가"였다(app/pricing.py 참고).
    assert by_key[("sql_database", "mcp-db-8")].spec == {"engine": "MySQL", "region": "koreacentral"}
    assert by_key[("sql_database", "mcp-pg-9")].spec == {"engine": "PostgreSQL", "region": "eastus"}
    assert by_key[("sql_database", "mcp-mssql-10")].spec == {"engine": "SQL Server", "region": "eastus"}
    # Storage는 정가표에 없는 사용량 기반 서비스라 spec을 안 채운다(기존 동작 유지).
    assert storage.spec == {}

    cdn = by_key[("cdn", "mcp-cdn01-11-fd-profile")]
    assert cdn.original_resource_type == "Microsoft.Cdn/profiles"
    assert cdn.region is None  # 전역 리소스
    assert cdn.status == "DEPLOYED"
    assert cdn.spec == {}

    # disks/generic VM/afdEndpoints 행은 걸러졌다 — 총 6건(VM 1 + Storage 1 + SQL 3 + CDN 1).
    assert len(result) == 6


def test_provisioning_key_matches_discovery_for_storage():
    """프로비저닝이 만드는 provider_resource_key와 discover가 만들 키가 일치하는지 고정한다.

    routers/provisioning.py의 `_resource_attrs`가 azure/storage_account에 대해 external_id=계정
    이름을 반환하고, sync_jobs.py는 `{provider}:{service_code}:{external_id}`로 키를 만든다.
    두 경로가 어긋나면 방금 만든 스토리지가 매 동기화마다 stale로 찍힌다(이 버그를 막는 회귀 테스트).
    """
    from app.routers.provisioning import _resource_attrs

    external_id, resource_type, _region, name = _resource_attrs(
        "azure", "storage_account", {"account_name": "mcptestmcps322"}, {}, {"region": "eastus"}
    )
    prov_key = f"azure:storage_account:{external_id}"
    disc_key = "azure:storage_account:mcptestmcps322"
    assert prov_key == disc_key
    assert name == "mcptestmcps322"


def test_provisioning_key_matches_discovery_for_cdn():
    """Azure CDN은 프로비저닝이 route가 아니라 Front Door **profile** 이름을 식별자로 저장해야
    동기화(profile만 열거 가능)와 맞물린다. route_name을 쓰면 첫 동기화에 stale로 사라진다."""
    from app.routers.provisioning import _resource_attrs

    external_id, resource_type, region, name = _resource_attrs(
        "azure", "cdn",
        {"profile_name": "mcp-cdn01-11-fd-profile", "route_name": "mcp-cdn01-11-route"},
        {}, {},
    )
    assert external_id == "mcp-cdn01-11-fd-profile"  # route_name이 아니다
    assert resource_type == "Microsoft.Cdn/profiles"
    assert region is None
    assert f"azure:cdn:{external_id}" == "azure:cdn:mcp-cdn01-11-fd-profile"


def test_returns_empty_on_incomplete_secret(monkeypatch):
    def _raise(**kw):
        raise KeyError("tenant_id")

    monkeypatch.setattr(azure, "ClientSecretCredential", _raise)
    assert azure.discover_resources({}, "sub-1") == []
