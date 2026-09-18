from __future__ import annotations

import datetime as dt
import time
from urllib.parse import quote

import requests
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.cloud import compute_v1, resourcemanager_v3
from google.oauth2 import service_account

from app.providers import VerificationResult

_CLOUD_PLATFORM_SCOPE = ["https://www.googleapis.com/auth/cloud-platform"]

# databaseVersion 접두사 -> pricing.py/gcp_cloudsql_provisioning.py가 쓰는 engine 이름.
# 순서가 중요하다 — "SQLSERVER"를 "POSTGRES"보다 먼저 검사할 필요는 없지만 접두사가 겹치지
# 않으므로 순서 무관하다. 매칭 안 되는 값은 spec 없이(가격 추정 건너뜀) 둔다.
_CLOUD_SQL_ENGINE_PREFIXES = {
    "MYSQL": "MySQL",
    "POSTGRES": "PostgreSQL",
    "SQLSERVER": "SQL Server",
}


def _cloud_sql_engine_from_database_version(database_version: str | None) -> str | None:
    if not database_version:
        return None
    for prefix, engine in _CLOUD_SQL_ENGINE_PREFIXES.items():
        if database_version.startswith(prefix):
            return engine
    return None


def _compute_zone_to_region(zone_name: str) -> str | None:
    """"asia-northeast3-a" -> "asia-northeast3" (pricing.py의 region 키와 맞춘다).
    인벤토리 화면에 보이는 resources.region(zone 그대로)은 건드리지 않는다 — 이 변환은
    spec 안에서만 쓴다."""
    if not zone_name or "-" not in zone_name:
        return None
    return zone_name.rsplit("-", 1)[0]


def _compute_machine_type_from_url(machine_type_url: str | None) -> str | None:
    if not machine_type_url:
        return None
    return machine_type_url.rstrip("/").rsplit("/", 1)[-1] or None


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
    force_empty: bool = False,
) -> None:
    """GCP는 인스턴스가 zone 단위로 존재한다 — 이 스키마에는 zone 컬럼이 따로 없어
    `resources.region` 값을 zone으로 그대로 사용한다(예: `asia-northeast3-a`).

    2026-09-17 확장 — 이전엔 Compute Engine만 지원했다. AWS가 RDS(start/stop/delete)·
    S3(delete)까지 지원하는 것과 격차가 있어(실사용자 확인), Cloud SQL/Storage/CDN도 추가한다
    (`resource_actions.py`의 `supported_actions()`가 서비스별 허용 동작을 결정한다 — 이 함수는
    이미 허용된 조합만 받는다는 전제로 짜여 있다).

    Cloud SQL/Storage/CDN은 discover_resources()와 동일하게 전용 클라이언트 라이브러리가 없어
    `AuthorizedSession`으로 REST를 직접 호출한다."""
    from app.resource_actions import ResourceActionError

    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
    except (ValueError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc

    if service_code == "compute_engine":
        if not region:
            raise ResourceActionError("PROVIDER_API_ERROR")
        try:
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
        return

    session = AuthorizedSession(credentials.with_scopes(_CLOUD_PLATFORM_SCOPE))

    if service_code == "cloud_sql":
        _cloud_sql_action(session, external_account_id, external_resource_id, action)
        return
    if service_code == "cloud_storage":
        _cloud_storage_action(session, external_resource_id, action, force_empty)
        return
    if service_code == "cloud_cdn":
        _cloud_cdn_action(session, external_account_id, external_resource_id, action)
        return

    raise ResourceActionError("UNSUPPORTED_OPERATION")


def _raise_for_rest_error(resp: requests.Response) -> None:
    from app.resource_actions import ResourceActionError

    if resp.status_code >= 400:
        raise ResourceActionError("PROVIDER_API_ERROR", resp.text[:500])


def _cloud_sql_action(session: AuthorizedSession, project_id: str, instance_name: str, action: str) -> None:
    """Cloud SQL Admin API에는 전용 start/stop 엔드포인트가 없다 — `settings.activationPolicy`를
    `ALWAYS`(start)/`NEVER`(stop)로 PATCH하는 것이 GCP가 문서화한 방식이다. RDS와 동일하게
    호출이 accept되면 성공으로 본다(작업 완료까지 폴링하지 않음)."""
    from app.resource_actions import ResourceActionError

    base = f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{project_id}/instances/{instance_name}"
    try:
        if action == "start":
            resp = session.patch(base, json={"settings": {"activationPolicy": "ALWAYS"}})
        elif action == "stop":
            resp = session.patch(base, json={"settings": {"activationPolicy": "NEVER"}})
        else:
            resp = session.delete(base)
    except requests.RequestException as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    _raise_for_rest_error(resp)


def _list_bucket_objects(session: AuthorizedSession, bucket_name: str):
    page_token = None
    while True:
        params = {"pageToken": page_token} if page_token else {}
        resp = session.get(f"https://storage.googleapis.com/storage/v1/b/{bucket_name}/o", params=params)
        resp.raise_for_status()
        data = resp.json()
        for obj in data.get("items", []):
            yield obj["name"]
        page_token = data.get("nextPageToken")
        if not page_token:
            return


def _empty_bucket(session: AuthorizedSession, bucket_name: str) -> None:
    """`_empty_bucket`(aws.py)의 GCS 버전 — 객체를 전부 지운 뒤에야 버킷 자체를 지울 수 있다."""
    for name in list(_list_bucket_objects(session, bucket_name)):
        encoded = quote(name, safe="")
        session.delete(f"https://storage.googleapis.com/storage/v1/b/{bucket_name}/o/{encoded}")


def _cloud_storage_action(session: AuthorizedSession, bucket_name: str, action: str, force_empty: bool) -> None:
    """S3의 `delete`만 지원 정책과 동일 — 버킷엔 시작/중지 개념이 없다. 비어있지 않으면
    S3와 동일하게 `BucketNotEmpty`를 던지고, `force_empty=true`면 먼저 비우고 재시도한다."""
    from app.resource_actions import ResourceActionError

    if action != "delete":
        raise ResourceActionError("UNSUPPORTED_OPERATION")

    url = f"https://storage.googleapis.com/storage/v1/b/{bucket_name}"
    try:
        resp = session.delete(url)
        if resp.status_code == 409:
            if not force_empty:
                raise ResourceActionError("BucketNotEmpty")
            _empty_bucket(session, bucket_name)
            resp = session.delete(url)
    except requests.RequestException as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    _raise_for_rest_error(resp)


def _cloud_cdn_action(session: AuthorizedSession, project_id: str, rule_name: str, action: str) -> None:
    """로드밸런서 forwarding rule엔 시작/중지 개념이 없다 — S3·GCS 버킷과 같은 이유로 delete만
    지원한다. discover_resources()가 이 리소스를 조회하는 엔드포인트와 짝을 맞춘다.

    **forwarding rule만 지우면 잔존 리소스가 남는다**(2026-09-17, 실사용 테스트로 발견 —
    `terraform/gcp/cloud_cdn/main.tf`가 forwarding rule 아래에 target_http_proxy·url_map·
    backend_bucket까지 3개를 더 만든다). 이 셋은 항상 이 CDN 하나를 위해서만 만들어지는
    전용 리소스라(공유되지 않음) 함께 지워도 안전하다 — terraform 리소스 이름 규칙
    (`{instance_name}[-urlmap|-http-proxy]`, forwarding rule은 `{instance_name}-fwd-rule`)에서
    `instance_name`을 역산해 의존성 역순(rule → proxy → urlmap → backend bucket)으로 지운다.

    **백엔드로 연결된 GCS 버킷(원본 콘텐츠)은 여기서 지우지 않는다** — `create_bucket=false`로
    기존 버킷을 연결했을 수도 있어(`app/gcp_cdn_provisioning.py` 참고), 우리가 만들지 않았을
    수도 있는 버킷을 자동으로 지우는 위험을 감수하지 않는다. 이 버킷은 `discover_resources()`가
    `cloud_storage`로 따로 추적하므로, 정말 이 CDN 전용으로 만들어진 버킷이었다면 인벤토리에
    별도 행으로 계속 보이고 사용자가 원할 때 그 행에서 직접 지우면 된다.

    **각 삭제가 실제로 끝날 때까지 기다려야 한다**(2026-09-17, 실사용 테스트로 발견) — GCP는
    forwarding rule의 delete Operation이 `DONE`이 되기 전까지는 그게 가리키는
    target_http_proxy의 삭제를 거부한다(`RESOURCE_IN_USE_BY_ANOTHER_RESOURCE`). RDS/EC2처럼
    "요청이 accept되면 성공"으로 보는 비대칭 정책은 서로 의존하는 리소스 체인에는 적용할 수
    없어, 여기서만 `_wait_for_global_operation()`으로 한 단계씩 완료를 확인하고 다음으로
    넘어간다."""
    from app.resource_actions import ResourceActionError

    if action != "delete":
        raise ResourceActionError("UNSUPPORTED_OPERATION")

    base = "https://compute.googleapis.com/compute/v1/projects/" + project_id + "/global"

    try:
        resp = session.delete(f"{base}/forwardingRules/{rule_name}")
    except requests.RequestException as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    _raise_for_rest_error(resp)
    _wait_for_global_operation(session, project_id, resp)

    if not rule_name.endswith("-fwd-rule"):
        return  # 우리 terraform 모듈이 만든 이름 규칙이 아니면(수동 생성 등) 부수 정리를 시도하지 않는다.
    instance_name = rule_name[: -len("-fwd-rule")]

    # 이 셋은 전부 이 CDN 전용이라 실패해도 요청한 리소스(forwarding rule)는 이미 지워졌으므로
    # 전체 액션을 실패로 돌리지 않는다 — best-effort 정리(이미 지워졌으면 404, 그대로 무시).
    for path in (
        f"targetHttpProxies/{instance_name}-http-proxy",
        f"urlMaps/{instance_name}-urlmap",
        f"backendBuckets/{instance_name}",
    ):
        try:
            del_resp = session.delete(f"{base}/{path}")
            _wait_for_global_operation(session, project_id, del_resp)
        except requests.RequestException:
            pass


def _wait_for_global_operation(session: AuthorizedSession, project_id: str, delete_response: requests.Response, timeout: float = 90) -> None:
    """전역(global) 리소스 delete 응답이 돌려준 Operation이 `DONE`이 될 때까지 기다린다."""
    try:
        operation_name = delete_response.json().get("name")
    except ValueError:
        return
    if not operation_name:
        return

    url = f"https://compute.googleapis.com/compute/v1/projects/{project_id}/global/operations/{operation_name}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            op_resp = session.get(url)
        except requests.RequestException:
            return
        if op_resp.status_code != 200 or op_resp.json().get("status") == "DONE":
            return
        time.sleep(2)


def discover_resources(secret_payload: dict, project_id: str) -> list:
    """Compute Engine·Cloud SQL·Cloud Storage·Cloud CDN을 동기화한다.

    2026-09-16 확장 — 이전엔 Compute Engine만 지원했다(§9 결정, resource_actions.py와 범위를
    맞춤). 그런데 GCP 동기화는 "이번 조회에서 안 보인 리소스는 is_stale=true로 표시"하는 방식이라
    (`app/routers/sync_jobs.py`의 `_mark_stale_resources`), Cloud SQL/Storage/CDN은 애초에
    조회 대상이 아니라서 **막 생성한 진짜 리소스도 동기화 한 번이면 곧바로 "사라진 것"으로
    표시돼 인벤토리에서 안 보이는 문제**가 있었다(실사용 테스트로 발견). 그래서 나머지 3개
    서비스도 실제로 조회하도록 확장한다 — 이 시점엔 `resource_actions.py`(시작/중지/삭제 지원
    범위)가 아직 Compute Engine만 지원해 조회와 제어 범위가 어긋났었는데, 이후
    `perform_resource_action()`도 Cloud SQL/Storage/CDN까지 확장해 둘의 범위를 맞췄다
    (2026-09-17, `gwonhyung/be-gcp-resource-actions`).

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
                spec = {}
                machine_type = _compute_machine_type_from_url(instance.machine_type)
                region = _compute_zone_to_region(zone_name)
                if machine_type and region:
                    spec = {"instance_type": machine_type, "region": region}
                results.append(
                    DiscoveredResource(
                        service_code="compute_engine",
                        external_resource_id=instance.name,
                        original_resource_type="Compute Engine Instance",
                        name=instance.name,
                        region=zone_name,
                        status=(instance.status or "").upper() or None,
                        tags=dict(instance.labels) if instance.labels else {},
                        spec=spec,
                    )
                )
    except (GoogleAuthError, GoogleAPICallError):
        pass  # 이 서비스 하나가 막혀도(API 비활성화 등) 나머지 서비스는 계속 조회한다.

    # AuthorizedSession으로 REST를 직접 부를 땐 scope를 명시해야 한다 — compute_v1 같은 typed
    # 클라이언트는 내부적으로 기본 scope를 넣어주지만, 순수 credentials 객체는 scope가 없으면
    # 토큰 발급 단계에서 "invalid_scope"로 실패한다(실사용 테스트로 발견).
    scoped_credentials = credentials.with_scopes(_CLOUD_PLATFORM_SCOPE)
    session = AuthorizedSession(scoped_credentials)

    try:
        resp = session.get(f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{project_id}/instances")
        if resp.status_code == 200:
            for instance in resp.json().get("items", []):
                spec = {}
                engine = _cloud_sql_engine_from_database_version(instance.get("databaseVersion"))
                region = instance.get("region")
                if engine and region:
                    spec = {"engine": engine, "region": region}
                results.append(
                    DiscoveredResource(
                        service_code="cloud_sql",
                        external_resource_id=instance["name"],
                        original_resource_type="Cloud SQL Instance",
                        name=instance["name"],
                        region=region,
                        status=(instance.get("state") or "").upper() or None,
                        tags=dict((instance.get("settings") or {}).get("userLabels") or {}),
                        spec=spec,
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


def _firewall_rule_out(f) -> dict:
    allowed_or_denied = list(f.allowed) + list(f.denied)
    protocol = allowed_or_denied[0].I_p_protocol if allowed_or_denied else None
    ports: list[str] = []
    for entry in allowed_or_denied:
        ports.extend(entry.ports)
    return {
        "name": f.name,
        "network": f.network,
        "direction": f.direction,
        "priority": f.priority,
        "action": "deny" if f.denied else "allow",
        "protocol": protocol,
        "ports": ports,
        "source_ranges": list(f.source_ranges),
        "target_tags": list(f.target_tags),
    }


def list_firewall_rules(secret_payload: dict, project_id: str) -> list[dict]:
    """보안그룹 관리 화면(2026-09-17)의 목록 조회 — GCP는 "그룹"이 없다. VPC 전역(global)
    방화벽 규칙 자체가 최상위 객체라, AWS SG/Azure NSG처럼 그룹 밑에 규칙이 중첩된 모양이
    아니라 평면 목록이다(`docs/Security_Group_Management_Design_2026-09-17.md` 참고)."""
    from app.resource_actions import ResourceActionError

    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
        client = compute_v1.FirewallsClient(credentials=credentials)
        rules = [_firewall_rule_out(f) for f in client.list(project=project_id)]
    except (GoogleAuthError, GoogleAPICallError, ValueError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    return rules


def create_firewall_rule(secret_payload: dict, project_id: str, rule: dict) -> dict:
    from app.resource_actions import ResourceActionError

    # allowed/denied는 서로 다른 메시지 타입이라(둘 다 필드는 I_p_protocol/ports로 같지만) 액션에
    # 맞는 쪽만 채운다 — 반대쪽에 잘못된 타입을 넣으면 즉시 TypeError.
    if rule["action"] == "deny":
        firewall = compute_v1.Firewall(
            name=rule["name"],
            network=rule["network"],
            direction=rule["direction"],
            priority=rule["priority"],
            source_ranges=rule.get("source_ranges") or [],
            target_tags=rule.get("target_tags") or [],
            denied=[compute_v1.Denied(I_p_protocol=rule["protocol"], ports=rule.get("ports") or [])],
        )
    else:
        firewall = compute_v1.Firewall(
            name=rule["name"],
            network=rule["network"],
            direction=rule["direction"],
            priority=rule["priority"],
            source_ranges=rule.get("source_ranges") or [],
            target_tags=rule.get("target_tags") or [],
            allowed=[compute_v1.Allowed(I_p_protocol=rule["protocol"], ports=rule.get("ports") or [])],
        )
    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
        client = compute_v1.FirewallsClient(credentials=credentials)
        client.insert(project=project_id, firewall_resource=firewall).result()
        created = client.get(project=project_id, firewall=rule["name"])
    except (GoogleAuthError, GoogleAPICallError, ValueError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    return _firewall_rule_out(created)


def delete_firewall_rule(secret_payload: dict, project_id: str, name: str) -> None:
    from app.resource_actions import ResourceActionError

    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
        client = compute_v1.FirewallsClient(credentials=credentials)
        client.delete(project=project_id, firewall=name).result()
    except (GoogleAuthError, GoogleAPICallError, ValueError, KeyError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc


def get_cpu_utilization(secret_payload: dict, project_id: str, instance_names: list[str]) -> dict[str, float | None]:
    """최근 CPU 사용률(%)을 Compute Engine 인스턴스 이름별로 조회한다 — 보고서 "리소스 사용률
    상위" 섹션(2026-09-17)의 실데이터 소스.

    **2026-09-18 복원**: PR #87(보안그룹 관리 기능)이 이 함수를 실수로 지웠는데(`app/metrics.py`는
    계속 이 함수를 호출해 GCP 리소스가 있으면 `GET /resources/utilization/top`이 500으로
    터지고 있었다 — 통합테스트 중 발견), 방화벽 규칙 함수들과 겹치는 부분 없이 그대로 복원한다.

    **GCP만 이름이 아니라 숫자 instance_id로 조회해야 한다**: Cloud Monitoring의
    `compute.googleapis.com/instance/cpu/utilization` 시계열은 `resource.labels.instance_id`
    (숫자)로만 필터링할 수 있고 인스턴스 이름 라벨이 없다. 그런데 `discover_resources()`는
    이름(`instance.name`)을 `external_resource_id`로 저장한다 — 그래서 여기서 먼저
    `compute_v1.aggregated_list`로 이름→숫자 ID 매핑을 만든 다음, 그 ID로 시계열을 찾아 다시
    이름으로 돌려준다.

    메모리는 다루지 않는다(Ops Agent 설치 전제) — AWS/Azure와 같은 이유(각 provider 모듈 참고)."""
    if not instance_names:
        return {}

    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
    except (ValueError, KeyError):
        return {name: None for name in instance_names}

    out: dict[str, float | None] = {name: None for name in instance_names}
    wanted = set(instance_names)
    id_to_name: dict[str, str] = {}
    try:
        client = compute_v1.InstancesClient(credentials=credentials)
        for _zone, scoped_list in client.aggregated_list(project=project_id):
            for instance in scoped_list.instances or []:
                if instance.name in wanted:
                    id_to_name[str(instance.id)] = instance.name
    except (GoogleAuthError, GoogleAPICallError):
        return out
    if not id_to_name:
        return out

    session = AuthorizedSession(credentials.with_scopes(_CLOUD_PLATFORM_SCOPE))
    now = dt.datetime.now(dt.timezone.utc)
    params = {
        "filter": 'metric.type="compute.googleapis.com/instance/cpu/utilization"',
        "interval.startTime": (now - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval.endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        resp = session.get(f"https://monitoring.googleapis.com/v3/projects/{project_id}/timeSeries", params=params)
        if resp.status_code == 200:
            # Cloud Monitoring도 기본적으로 최신 시각 순(내림차순)으로 points를 돌려준다.
            for series in resp.json().get("timeSeries", []):
                instance_id = ((series.get("resource") or {}).get("labels") or {}).get("instance_id")
                name = id_to_name.get(instance_id)
                if not name:
                    continue
                points = series.get("points") or []
                if not points:
                    continue
                value = (points[0].get("value") or {}).get("doubleValue")
                if value is not None:
                    out[name] = round(value * 100, 1)  # GCP는 0~1 비율로 반환 → %로 환산
    except requests.RequestException:
        pass
    return out
