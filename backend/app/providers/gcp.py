from __future__ import annotations

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import compute_v1, resourcemanager_v3
from google.oauth2 import service_account

from app.providers import VerificationResult


def verify(external_account_id: str, secret_payload: dict) -> VerificationResult:
    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
        client = resourcemanager_v3.ProjectsClient(credentials=credentials)
        client.get_project(name=f"projects/{external_account_id}")
    except (GoogleAuthError, GoogleAPICallError, ValueError, KeyError):
        return VerificationResult(verified=False, error_code="PROVIDER_AUTHENTICATION_FAILED")

    scope = {"inventory_read": False, "resource_control": False, "provision": False, "cost_read": False}

    try:
        instances_client = compute_v1.InstancesClient(credentials=credentials)
        next(iter(instances_client.aggregated_list(project=external_account_id)), None)
        scope["inventory_read"] = True
    except (GoogleAPICallError, GoogleAuthError):
        pass

    return VerificationResult(verified=True, permission_scope=scope)


def perform_resource_action(
    service_code: str,
    action: str,
    secret_payload: dict,
    external_account_id: str,
    region: str | None,
    external_resource_id: str,
) -> None:
    """GCP는 인스턴스가 zone 단위로 존재한다 — 이 스키마에는 zone 컬럼이 따로 없어
    `resources.region` 값을 zone으로 그대로 사용한다(예: `asia-northeast3-a`)."""
    from app.resource_actions import ResourceActionError

    if service_code != "compute_engine":
        raise ResourceActionError("UNSUPPORTED_OPERATION")
    if not region:
        raise ResourceActionError("PROVIDER_API_ERROR")

    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
        client = compute_v1.InstancesClient(credentials=credentials)
        if action == "start":
            operation = client.start(project=external_account_id, zone=region, instance=external_resource_id)
        elif action == "stop":
            operation = client.stop(project=external_account_id, zone=region, instance=external_resource_id)
        else:
            operation = client.delete(project=external_account_id, zone=region, instance=external_resource_id)
        operation.result()
    except (GoogleAuthError, GoogleAPICallError, ValueError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc


def discover_resources(secret_payload: dict, project_id: str) -> list:
    """Compute Engine 인스턴스만 동기화한다(§9 지원 범위는 resource_actions.py와 동일)."""
    from app.resource_sync import DiscoveredResource

    results: list[DiscoveredResource] = []
    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
        client = compute_v1.InstancesClient(credentials=credentials)
        for zone, scoped_list in client.aggregated_list(project=project_id):
            if not scoped_list.instances:
                continue
            zone_name = zone.split("/")[-1]
            for instance in scoped_list.instances:
                results.append(
                    DiscoveredResource(
                        service_code="compute_engine",
                        external_resource_id=instance.name,
                        original_resource_type="Compute Engine Instance",
                        name=instance.name,
                        region=zone_name,
                        status=(instance.status or "").upper() or None,
                        tags=dict(instance.labels) if instance.labels else {},
                    )
                )
    except (GoogleAuthError, GoogleAPICallError, ValueError, KeyError):
        pass

    return results
