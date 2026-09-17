from __future__ import annotations

import requests
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
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
    """Compute Engine·Cloud SQL·Cloud Storage·Cloud CDN을 동기화한다.

    2026-09-16 확장 — 이전엔 Compute Engine만 지원했다(§9 결정, resource_actions.py와 범위를
    맞춤). 그런데 GCP 동기화는 "이번 조회에서 안 보인 리소스는 is_stale=true로 표시"하는 방식이라
    (`app/routers/sync_jobs.py`의 `_mark_stale_resources`), Cloud SQL/Storage/CDN은 애초에
    조회 대상이 아니라서 **막 생성한 진짜 리소스도 동기화 한 번이면 곧바로 "사라진 것"으로
    표시돼 인벤토리에서 안 보이는 문제**가 있었다(실사용 테스트로 발견). 그래서 나머지 3개
    서비스도 실제로 조회하도록 확장한다 — `resource_actions.py`(시작/중지/삭제 지원 범위)는
    이 결정과 무관하게 그대로 Compute Engine만 지원한다(조회와 제어는 별개 결정).

    Cloud SQL/Storage/CDN은 `google-cloud-*` 전용 클라이언트 라이브러리가 없거나(Cloud SQL
    Admin은 애초에 그런 라이브러리가 없음) 새 의존성을 추가할 정도는 아니라고 판단해, `google-auth`
    (이미 있는 의존성)의 `AuthorizedSession`으로 REST를 직접 호출한다 — 이번 세션에서 실제 GCP
    계정에 대고 같은 방식으로 여러 번 검증해 본 방법과 동일하다.
    """
    from app.resource_sync import DiscoveredResource

    results: list[DiscoveredResource] = []
    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
    except (ValueError, KeyError):
        return results

    try:
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
    except (GoogleAuthError, GoogleAPICallError):
        pass  # 이 서비스 하나가 막혀도(API 비활성화 등) 나머지 서비스는 계속 조회한다.

    # AuthorizedSession으로 REST를 직접 부를 땐 scope를 명시해야 한다 — compute_v1 같은 typed
    # 클라이언트는 내부적으로 기본 scope를 넣어주지만, 순수 credentials 객체는 scope가 없으면
    # 토큰 발급 단계에서 "invalid_scope"로 실패한다(실사용 테스트로 발견).
    scoped_credentials = credentials.with_scopes(["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(scoped_credentials)

    try:
        resp = session.get(f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{project_id}/instances")
        if resp.status_code == 200:
            for instance in resp.json().get("items", []):
                results.append(
                    DiscoveredResource(
                        service_code="cloud_sql",
                        external_resource_id=instance["name"],
                        original_resource_type="Cloud SQL Instance",
                        name=instance["name"],
                        region=instance.get("region"),
                        status=(instance.get("state") or "").upper() or None,
                        tags=dict((instance.get("settings") or {}).get("userLabels") or {}),
                    )
                )
    except (requests.RequestException, ValueError, KeyError):
        pass

    try:
        resp = session.get(f"https://storage.googleapis.com/storage/v1/b?project={project_id}")
        if resp.status_code == 200:
            for bucket in resp.json().get("items", []):
                results.append(
                    DiscoveredResource(
                        service_code="cloud_storage",
                        external_resource_id=bucket["name"],
                        original_resource_type="Cloud Storage Bucket",
                        name=bucket["name"],
                        # GCS는 location을 대문자로 돌려준다(예: "ASIA-NORTHEAST3") — 생성 시
                        # 저장하는 소문자 관례와 맞춘다(gcp_storage_provisioning.py와 동일 이유).
                        region=(bucket.get("location") or "").lower() or None,
                        status="AVAILABLE",
                        tags=dict(bucket.get("labels") or {}),
                    )
                )
    except (requests.RequestException, ValueError, KeyError):
        pass

    try:
        resp = session.get(f"https://compute.googleapis.com/compute/v1/projects/{project_id}/global/forwardingRules")
        if resp.status_code == 200:
            for rule in resp.json().get("items", []):
                results.append(
                    DiscoveredResource(
                        service_code="cloud_cdn",
                        external_resource_id=rule["name"],
                        original_resource_type="Cloud CDN (HTTP LB)",
                        name=rule["name"],
                        region=None,  # 로드밸런서 리소스는 전역(global) — CloudFront/Front Door와 동일 원칙.
                        status="DEPLOYED",
                        tags={},
                    )
                )
    except (requests.RequestException, ValueError, KeyError):
        pass

    return results


def list_network_resources(secret_payload: dict, project_id: str) -> dict:
    """프로비저닝 폼의 "기존 리소스 사용"에서 실제 VPC 네트워크 목록을 보여주기 위한 조회 전용
    API(2026-09-17). GCP 네트워크는 전역(global) 리소스라 AWS(리전 필요)와 달리 region 파라미터가
    없다. 실패 시 빈 목록이 아니라 예외를 올린다(app/providers/aws.py와 동일 원칙)."""
    from app.resource_actions import ResourceActionError

    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
        client = compute_v1.NetworksClient(credentials=credentials)
        networks = [
            {"name": n.name, "self_link": n.self_link, "auto_create_subnetworks": n.auto_create_subnetworks}
            for n in client.list(project=project_id)
        ]
    except (GoogleAuthError, GoogleAPICallError, ValueError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc

    return {"networks": networks}
