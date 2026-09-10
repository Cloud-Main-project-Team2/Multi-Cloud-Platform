from __future__ import annotations

import re

from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
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
