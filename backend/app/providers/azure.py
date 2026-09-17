from __future__ import annotations

import re

from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.resource.subscriptions import SubscriptionClient

from app.providers import VerificationResult

# 리소스는 Azure ARM 리소스 ID 전체(subscription/resourceGroup 포함)를 external_resource_id로
# 저장한다고 가정한다 — Azure는 VM 이름만으로는 조작에 필요한 resource group을 알 수 없다.
_ARM_ID_RE = re.compile(
    r"/subscriptions/(?P<sub>[^/]+)/resourceGroups/(?P<rg>[^/]+)/providers/[^/]+/[^/]+/(?P<name>[^/]+)$",
    re.IGNORECASE,
)


def verify(external_account_id: str, secret_payload: dict) -> VerificationResult:
    try:
        credential = ClientSecretCredential(
            tenant_id=secret_payload["tenant_id"],
            client_id=secret_payload["client_id"],
            client_secret=secret_payload["client_secret"],
        )
        subscription_client = SubscriptionClient(credential)
        subscription_client.subscriptions.get(external_account_id)
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError):
        return VerificationResult(verified=False, error_code="PROVIDER_AUTHENTICATION_FAILED")

    scope = {"inventory_read": False, "resource_control": False, "provision": False, "cost_read": False}

    try:
        resource_client = ResourceManagementClient(credential, external_account_id)
        next(iter(resource_client.resources.list()), None)
        scope["inventory_read"] = True
    except (ClientAuthenticationError, HttpResponseError, AzureError):
        pass

    return VerificationResult(verified=True, permission_scope=scope)


def perform_resource_action(service_code: str, action: str, secret_payload: dict, external_resource_id: str) -> None:
    from app.resource_actions import ResourceActionError

    if service_code != "vm":
        raise ResourceActionError("UNSUPPORTED_OPERATION")

    match = _ARM_ID_RE.search(external_resource_id)
    if not match:
        raise ResourceActionError("PROVIDER_API_ERROR")
    subscription_id, resource_group, vm_name = match.group("sub"), match.group("rg"), match.group("name")

    try:
        credential = ClientSecretCredential(
            tenant_id=secret_payload["tenant_id"],
            client_id=secret_payload["client_id"],
            client_secret=secret_payload["client_secret"],
        )
        compute_client = ComputeManagementClient(credential, subscription_id)
        if action == "start":
            poller = compute_client.virtual_machines.begin_start(resource_group, vm_name)
        elif action == "stop":
            poller = compute_client.virtual_machines.begin_power_off(resource_group, vm_name)
        else:
            poller = compute_client.virtual_machines.begin_delete(resource_group, vm_name)
        poller.result()
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc


def discover_resources(secret_payload: dict, subscription_id: str) -> list:
    """VM만 동기화한다(§9 지원 범위는 resource_actions.py와 동일 — CLAUDE.md 기록).

    실제 전원 상태(instance view)는 VM마다 별도 API 호출이 필요해 비용이 커서 이번 세션에서는
    생략하고 목록 API의 `provisioning_state`(ARM 리소스 생성 상태 — RUNNING/STOPPED 같은 실제
    전원 상태가 아니다)를 대신 넣는다."""
    from app.resource_sync import DiscoveredResource

    results: list[DiscoveredResource] = []
    try:
        credential = ClientSecretCredential(
            tenant_id=secret_payload["tenant_id"],
            client_id=secret_payload["client_id"],
            client_secret=secret_payload["client_secret"],
        )
        compute_client = ComputeManagementClient(credential, subscription_id)
        for vm in compute_client.virtual_machines.list_all():
            results.append(
                DiscoveredResource(
                    service_code="vm",
                    external_resource_id=vm.id,
                    original_resource_type="Virtual Machine",
                    name=vm.name,
                    region=vm.location,
                    status=(vm.provisioning_state or "").upper() or None,
                    tags=dict(vm.tags or {}),
                )
            )
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError):
        pass

    return results


def list_network_resources(secret_payload: dict, subscription_id: str) -> dict:
    """프로비저닝 폼의 "기존 리소스 사용"에서 실제 리소스 그룹/VNet/NSG 목록을 보여주기 위한
    조회 전용 API(2026-09-17). AWS와 달리 리전 파라미터가 없다 — 리소스 그룹/VNet/NSG는 구독
    전체에서 조회하고 각 항목의 location(리전)을 결과에 그대로 담아 보여준다.

    실패 시 빈 목록이 아니라 예외를 올린다(app/providers/aws.py의 list_network_resources와
    동일 원칙 — "권한 없음"과 "진짜 없음"을 구분해야 한다)."""
    from app.resource_actions import ResourceActionError

    try:
        credential = ClientSecretCredential(
            tenant_id=secret_payload["tenant_id"],
            client_id=secret_payload["client_id"],
            client_secret=secret_payload["client_secret"],
        )
        resource_client = ResourceManagementClient(credential, subscription_id)
        network_client = NetworkManagementClient(credential, subscription_id)

        resource_groups = [
            {"name": rg.name, "location": rg.location} for rg in resource_client.resource_groups.list()
        ]
        virtual_networks = [
            {
                "id": vnet.id,
                "name": vnet.name,
                "resource_group": vnet.id.split("/")[4],
                "location": vnet.location,
                "address_space": list((vnet.address_space.address_prefixes if vnet.address_space else None) or []),
            }
            for vnet in network_client.virtual_networks.list_all()
        ]
        network_security_groups = [
            {"id": nsg.id, "name": nsg.name, "resource_group": nsg.id.split("/")[4], "location": nsg.location}
            for nsg in network_client.network_security_groups.list_all()
        ]

        # azure/vm은 (위임이 필요 없어) 기존 서브넷을 그대로 재사용할 수 있다 — VNet 목록만으론
        # 고를 수 없어 각 VNet 밑의 서브넷도 따로 나열한다. VNet마다 한 번씩 호출해야 해서
        # (subnets.list는 VNet 단위 API) VNet이 아주 많은 구독에선 느릴 수 있지만, 이 도구의
        # 조회 규모(개발/데모용 구독)에선 충분하다.
        subnets: list[dict] = []
        for vnet in network_client.virtual_networks.list_all():
            vnet_rg = vnet.id.split("/")[4]
            try:
                for subnet in network_client.subnets.list(vnet_rg, vnet.name):
                    subnets.append(
                        {
                            "id": subnet.id,
                            "name": subnet.name,
                            "vnet_name": vnet.name,
                            "resource_group": vnet_rg,
                            "address_prefix": subnet.address_prefix,
                        }
                    )
            except (ClientAuthenticationError, HttpResponseError, AzureError):
                continue
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc

    return {
        "resource_groups": resource_groups,
        "virtual_networks": virtual_networks,
        "network_security_groups": network_security_groups,
        "azure_subnets": subnets,
    }


def _network_client(secret_payload: dict, subscription_id: str) -> NetworkManagementClient:
    credential = ClientSecretCredential(
        tenant_id=secret_payload["tenant_id"],
        client_id=secret_payload["client_id"],
        client_secret=secret_payload["client_secret"],
    )
    return NetworkManagementClient(credential, subscription_id)


def _security_rule_out(rule) -> dict:
    return {
        "name": rule.name,
        "priority": rule.priority,
        "direction": rule.direction,
        "access": rule.access,
        "protocol": rule.protocol,
        "source_address_prefix": rule.source_address_prefix,
        "destination_port_range": rule.destination_port_range,
        "description": rule.description,
    }


def list_security_groups(secret_payload: dict, subscription_id: str) -> list[dict]:
    """보안그룹 관리 화면(2026-09-17)의 목록 조회 — NSG + 중첩된 보안 규칙 전체를 돌려준다.
    `list_network_resources()`의 `network_security_groups`(id/name/rg/location 요약)와 달리
    각 NSG의 `security_rules`를 함께 담는다(Azure SDK가 이미 중첩해서 주므로 AWS처럼 별도
    API를 한 번 더 부를 필요가 없다)."""
    from app.resource_actions import ResourceActionError

    try:
        network_client = _network_client(secret_payload, subscription_id)
        groups = [
            {
                "id": nsg.id,
                "name": nsg.name,
                "resource_group": nsg.id.split("/")[4],
                "location": nsg.location,
                "rules": [_security_rule_out(r) for r in (nsg.security_rules or [])],
            }
            for nsg in network_client.network_security_groups.list_all()
        ]
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    return groups


def create_security_group(secret_payload: dict, subscription_id: str, resource_group: str, name: str, location: str) -> dict:
    from app.resource_actions import ResourceActionError

    try:
        network_client = _network_client(secret_payload, subscription_id)
        poller = network_client.network_security_groups.begin_create_or_update(
            resource_group, name, {"location": location}
        )
        nsg = poller.result()
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    return {"id": nsg.id, "name": nsg.name, "resource_group": resource_group, "location": nsg.location, "rules": []}


def delete_security_group(secret_payload: dict, subscription_id: str, resource_group: str, name: str) -> None:
    from app.resource_actions import ResourceActionError

    try:
        network_client = _network_client(secret_payload, subscription_id)
        network_client.network_security_groups.begin_delete(resource_group, name).result()
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc


def add_security_group_rule(
    secret_payload: dict, subscription_id: str, resource_group: str, nsg_name: str, rule: dict
) -> dict:
    from app.resource_actions import ResourceActionError

    try:
        network_client = _network_client(secret_payload, subscription_id)
        poller = network_client.security_rules.begin_create_or_update(
            resource_group,
            nsg_name,
            rule["name"],
            {
                "priority": rule["priority"],
                "direction": rule["direction"],
                "access": rule["access"],
                "protocol": rule["protocol"],
                # source/destination 포트·주소 중 사용자가 실제로 고르는 건 source 주소와
                # destination 포트뿐이다(§3 스키마) — 나머지 절반은 "전체 허용"으로 고정한다.
                "source_port_range": "*",
                "destination_port_range": rule["destination_port_range"],
                "source_address_prefix": rule["source_address_prefix"],
                "destination_address_prefix": "*",
            },
        )
        created = poller.result()
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    return _security_rule_out(created)


def remove_security_group_rule(secret_payload: dict, subscription_id: str, resource_group: str, nsg_name: str, rule_name: str) -> None:
    from app.resource_actions import ResourceActionError

    try:
        network_client = _network_client(secret_payload, subscription_id)
        network_client.security_rules.begin_delete(resource_group, nsg_name, rule_name).result()
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
