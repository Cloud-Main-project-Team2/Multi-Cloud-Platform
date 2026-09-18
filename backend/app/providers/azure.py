from __future__ import annotations

import re
from datetime import timedelta

from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.resource.subscriptions import SubscriptionClient
from azure.monitor.query import MetricAggregationType, MetricsQueryClient

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


def get_cpu_utilization(secret_payload: dict, resource_ids: list[str]) -> dict[str, float | None]:
    """최근 1시간 평균 CPU 사용률(%)을 VM별로 조회한다 — 보고서 "리소스 사용률 상위" 섹션
    (2026-09-17)의 실데이터 소스. `Percentage CPU`는 에이전트 없이도 나오는 플랫폼 메트릭이다.

    `azure-mgmt-monitor`(`MonitorManagementClient`, 레거시 — MS 문서도 "archive-previous"로
    분류)가 아니라 현재 권장되는 `azure-monitor-query`(`MetricsQueryClient`)를 쓴다. 이미 쓰고
    있는 `azure-identity`의 `ClientSecretCredential`을 그대로 재사용할 수 있어 인증 코드가
    늘지 않는다.

    Azure Monitor Metrics API는 AWS/GCP와 달리 리소스 하나씩만 조회한다(공식 배치 조회는 같은
    리소스 유형·리전 제약이 커서 이번 범위에서는 단순하게 순회한다) — 이 앱 규모(리소스 수가
    적은 데모/과제 환경)에서는 직렬 호출로도 충분하다.

    메모리는 다루지 않는다(Azure Monitor Agent 설치 전제) — AWS/GCP와 같은 이유."""
    if not resource_ids:
        return {}

    try:
        credential = ClientSecretCredential(
            tenant_id=secret_payload["tenant_id"],
            client_id=secret_payload["client_id"],
            client_secret=secret_payload["client_secret"],
        )
        client = MetricsQueryClient(credential)
    except KeyError:
        return {resource_id: None for resource_id in resource_ids}

    out: dict[str, float | None] = {}
    for resource_id in resource_ids:
        try:
            response = client.query_resource(
                resource_id,
                metric_names=["Percentage CPU"],
                timespan=timedelta(hours=1),
                granularity=timedelta(minutes=5),
                aggregations=[MetricAggregationType.AVERAGE],
            )
            points = [
                (data.timestamp, data.average)
                for metric in response.metrics
                for series in metric.timeseries
                for data in series.data
                if data.average is not None
            ]
            points.sort(key=lambda p: p[0], reverse=True)
            out[resource_id] = round(points[0][1], 1) if points else None
        except (ClientAuthenticationError, HttpResponseError, AzureError):
            out[resource_id] = None
    return out


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
            vm_size = (vm.hardware_profile.vm_size if vm.hardware_profile else None) or None
            # pricing.py의 정가표는 "Standard_" 접두사 없는 SKU 이름을 키로 쓴다(provisioning.js가
            # 그 형태로 보내므로 — CLAUDE.md 기록). 이 접두사 제거는 여기(어댑터)에서만 한다 —
            # pricing.py 자체를 고치면 tests/test_pricing.py의 "Standard_B1s는 None" 기대가 깨진다.
            instance_type = vm_size.removeprefix("Standard_") if vm_size else None
            results.append(
                DiscoveredResource(
                    service_code="vm",
                    external_resource_id=vm.id,
                    original_resource_type="Virtual Machine",
                    name=vm.name,
                    region=vm.location,
                    status=(vm.provisioning_state or "").upper() or None,
                    tags=dict(vm.tags or {}),
                    spec={"instance_type": instance_type, "region": vm.location},
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
                            # 서브넷 자체엔 location이 없다 — 소속 VNet의 리전을 그대로 물려준다.
                            # 프론트가 "기존 리소스 사용"에서 실제로 만들 리소스와 같은 리전의
                            # 서브넷만 선택 가능하게 거르는 데 쓴다(2026-09-18 추가 — NIC와
                            # 서브넷의 리전이 다르면 Azure가 `InvalidResourceReference ... same
                            # region`으로 apply 단계에서 실패하는 걸 실사용 중 발견).
                            "location": vnet.location,
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


def list_vm_sku_availability(
    secret_payload: dict, subscription_id: str, region: str, sku_names: list[str]
) -> dict[str, dict]:
    """읽기 전용 VM SKU 가용성 사전 확인(2026-09-18) — 실제 생성 전에 "이 구독·리전에서 이
    SKU를 애초에 쓸 수 있는지"를 미리 걸러내기 위한 조회다. Azure 무료 체험 계정 안내가 말하는
    무료 대상 SKU(B1s/B2ats_v2/B2pts_v2)라도 실제 구독·리전에서 `SkuNotAvailable`
    (`Capacity Restrictions`)로 거부되는 사례가 있었다(2026-09-18 실측) — "무료 혜택 대상"과
    "지금 이 구독·리전에서 만들 수 있음"은 별개 질문이라 이 함수를 따로 둔다.

    `ComputeManagementClient.resource_skus.list(filter="location eq '<region>'")`는 (1) 그
    SKU가 이 리전에 아예 없는 경우와 (2) 리전엔 있지만 이 구독에 restriction이 걸린 경우를
    구분해서 알려준다 — 결과에 SKU 자체가 없으면 (1), 있는데 `restrictions`가 비어 있지 않으면
    (2, `NotAvailableForSubscription` 등)이다. `restrictions`가 빈 리스트면 이 구독·리전에서
    지금 만들 수 있다는 뜻이다.

    반환값은 `sku_names`의 각 이름을 키로 하는 dict이고, 값은
    `{"status": "available" | "restricted" | "not_offered_in_region", "reason": str | None}`.

    **조회 실패(SDK 예외)와 "restricted"는 절대 같은 의미가 아니다** — 호출부(라우터·프론트)는
    조회 자체가 실패하면 "확인 불가"로만 표시해야 하고 이를 "사용 가능"으로 간주해서는 안 된다.
    이 사전 확인은 조회 시점의 스냅샷일 뿐 실시간 용량(capacity)까지 보장하지 않는다 — 실제
    생성 시 Azure가 다시 검증하며, 이 조회가 "available"이었어도 생성이 거부될 수 있다."""
    from app.resource_actions import ResourceActionError

    try:
        credential = ClientSecretCredential(
            tenant_id=secret_payload["tenant_id"],
            client_id=secret_payload["client_id"],
            client_secret=secret_payload["client_secret"],
        )
        compute_client = ComputeManagementClient(credential, subscription_id)
        wanted = set(sku_names)
        found: dict[str, dict] = {}
        for sku in compute_client.resource_skus.list(filter=f"location eq '{region}'"):
            if sku.resource_type != "virtualMachines" or sku.name not in wanted:
                continue
            restrictions = sku.restrictions or []
            if restrictions:
                reason_codes = [r.reason_code for r in restrictions if getattr(r, "reason_code", None)]
                found[sku.name] = {
                    "status": "restricted",
                    "reason": ", ".join(str(r) for r in reason_codes) or "NotAvailableForSubscription",
                }
            else:
                found[sku.name] = {"status": "available", "reason": None}
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc

    return {name: found.get(name, {"status": "not_offered_in_region", "reason": None}) for name in sku_names}


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
