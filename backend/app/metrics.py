"""리소스 사용률(CPU) 실시간 조회 — 보고서_구현명세_v5.md §3.3 "리소스 사용률 상위" 섹션의
실데이터 소스(2026-09-17, `kwonhyeong/be-report-metrics`).

`resource_actions.py`가 provider별 액션 어댑터를 한곳에서 orchestrate하는 것과 같은 역할을
CPU 사용률 조회에 대해 한다 — 라우터는 이 모듈 하나만 호출하면 된다.

**이 값은 "그 보고서 기간의 값"이 아니라 "지금 이 순간의 값"이다.** `reports`/`cloud_resource_costs`
같은 저장 테이블이 아직 없어(2026-09-16 확인, CLAUDE.md 기록) 과거 시점 스냅샷을 만들 방법이
없다 — 그때그때 CSP Monitoring API를 호출해서 보여줄 뿐 어디에도 저장하지 않는다. 그래서
과거에 생성된 보고서를 다시 열어도 항상 "지금" 값이 나온다 — 프론트(report-view.js)가 이 차이를
문구로 반드시 표시해야 한다.

**메모리는 다루지 않는다** — CloudWatch Agent/Azure Monitor Agent/Ops Agent가 인스턴스에 설치돼
있어야만 나오는데, 이 앱의 Terraform 모듈(`terraform/{aws,azure,gcp}/.../main.tf`) 중 어느 것도
그 에이전트를 설치하지 않는다. `mem_percent`는 항상 None — 프론트가 "—"로 표시한다.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from app.models import CloudAccount, Credential, Resource, ServiceCatalog
from app.providers.session import CredentialResolutionError, resolve_secret_payload
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models import User

# resource_actions.py의 컴퓨트 계열 service_code와 동일 범위 — 모니터링 가능한 것도 이 셋뿐이다
# (Cloud SQL/RDS/Storage 등은 아직 다루지 않는다, 2026-09-17 결정: 보고서 §3.3이 요구하는
# "리소스 사용률 상위"는 CPU 기준이라 컴퓨트 인스턴스가 대상이다).
_COMPUTE_SERVICE_CODES = {"ec2": "aws", "vm": "azure", "compute_engine": "gcp"}


def get_top_utilization(db: "Session", user: "User", limit: int = 5) -> list[dict]:
    """로그인한 사용자의 컴퓨트 리소스 중 CPU 사용률 상위 N개를 실시간 조회해서 반환한다.

    자격 증명이 없거나(미등록) 검증 실패했거나 CSP 호출이 실패한 리소스는 `cpu_percent=None`으로
    담아 반환한다(항목 자체를 빼지 않는다 — 사용률 계산 실패와 "0%"를 구분하기 위해)."""
    items = _compute_items(db, user)
    # cpu_percent가 None인 항목(조회 실패)은 뒤로 — 정렬 자체가 실패를 "0%"로 오인하지 않게 한다.
    items.sort(key=lambda it: (it["cpu_percent"] is None, -(it["cpu_percent"] or 0)))
    return items[:limit]


# "미연결 디스크" 대상 원본 리소스 유형 — AWS만(providers/{azure,gcp}.py는 디스크를 별도
# 인벤토리 리소스로 추적하지 않는다, 2026-09-19 확인).
_UNATTACHED_DISK_TYPE = "EBS Volume"
_UNATTACHED_DISK_STATUS = "AVAILABLE"

# 이 값 미만이면 "유휴"로 본다 — get_top_utilization()과 같은 실시간 CPU 스냅샷 기준이라
# "며칠째 유휴"가 아니라 "지금 이 순간 낮다"는 뜻이다(모듈 docstring 참고, 시계열 저장이 없다).
_IDLE_CPU_PERCENT_THRESHOLD = 10.0


def get_unused_resources(db: "Session", user: "User", limit: int = 10) -> list[dict]:
    """보고서 §3.3 "미사용 리소스" 섹션의 실데이터 소스(2026-09-19, `kwonhyeong/be-next`).

    감지 범위는 딱 두 가지뿐이다 — 범위를 넓히지 않는다:

    - **미연결 디스크(AWS EBS Volume만)**: 이미 수집된 `resources.status`("AVAILABLE"=미연결,
      AWS `Volume.State` 그대로)를 그대로 읽을 뿐 추가 CSP 호출이 없다. "미연결 공인 IP"는 이번에
      넣지 않았다 — 어떤 provider adapter도 아직 Elastic IP/Public IP를 리소스로 수집하지 않는다
      (2026-09-19 grep 확인, `describe_addresses`류 호출 자체가 없음). "바로 구현 가능"은 디스크에만
      해당하고, IP는 `discover_resources()` 확장이 먼저 필요한 별도 작업이다.
    - **유휴 컴퓨트 인스턴스**: `get_top_utilization()`과 같은 실시간 CPU 조회를 재사용해 낮은
      순으로 담는다. 과거 이력이 아니라 "지금 이 순간 CPU가 threshold 미만"이라는 뜻이다.

    **유휴/미연결 기간**: EBS Volume은 `status_changed_at`(상태가 실제로 바뀐 시각, 비용 확장에서
    추가됨)이 있으면 그 값으로 실제 경과일을 계산한다. 필드 도입 이전부터 있던 행은 NULL이라
    추정하지 않고 그대로 None을 반환한다("0일"로 보이면 방금 풀렸다는 뜻이 되어 사실과 달라진다).
    컴퓨트 인스턴스는 `status_changed_at`이 "몇 시간째 idle인가"가 아니라 "몇 시간째 RUNNING인가"를
    뜻해 답이 다른 질문이므로(고성능 인스턴스가 몇 달간 고부하로 돌다가 방금 유휴해진 경우에도
    "몇 달째 유휴"로 잘못 보이게 된다) 여기서는 쓰지 않고 항상 None이다 — 지금 이 순간의 스냅샷일
    뿐임을 캡션에서 밝힌다.

    **월 예상 비용**: `resources.estimated_monthly_cost`(정가표 추정, 프로비저닝·동기화 시점에
    이미 계산돼 있음)를 그대로 쓴다. EBS Volume은 현재 정가표에 없어 대부분 None이다 — 단가를
    지어내지 않고 그대로 "정보 없음"으로 보여준다.
    """
    now = dt.datetime.now(dt.timezone.utc)
    items: list[dict] = []

    volume_rows = (
        db.query(Resource, CloudAccount)
        .join(CloudAccount, Resource.cloud_account_id == CloudAccount.id)
        .filter(
            CloudAccount.user_id == user.id,
            Resource.is_stale.is_(False),
            Resource.deleted_at.is_(None),
            CloudAccount.provider == "aws",
            Resource.original_resource_type == _UNATTACHED_DISK_TYPE,
            Resource.status == _UNATTACHED_DISK_STATUS,
        )
        .all()
    )
    for resource, account in volume_rows:
        idle_days = (now - resource.status_changed_at).days if resource.status_changed_at else None
        items.append(
            {
                "resource_id": str(resource.id),
                "provider": account.provider,
                "name": resource.name or resource.external_resource_id,
                "original_resource_type": resource.original_resource_type,
                "reason": "unattached_disk",
                "idle_days": idle_days,
                "estimated_monthly_cost": (
                    float(resource.estimated_monthly_cost) if resource.estimated_monthly_cost is not None else None
                ),
            }
        )

    for it in _compute_items(db, user):
        if it["cpu_percent"] is None or it["cpu_percent"] >= _IDLE_CPU_PERCENT_THRESHOLD:
            continue
        resource = db.get(Resource, int(it["resource_id"]))
        cost = resource.estimated_monthly_cost if resource is not None else None
        items.append(
            {
                "resource_id": it["resource_id"],
                "provider": it["provider"],
                "name": it["name"],
                "original_resource_type": it["original_resource_type"],
                "reason": "idle_compute",
                "idle_days": None,
                "estimated_monthly_cost": float(cost) if cost is not None else None,
            }
        )

    # 비용을 아는 항목(절감액이 확실한 항목)을 먼저 보여준다 — None은 뒤로.
    items.sort(key=lambda it: (it["estimated_monthly_cost"] is None, -(it["estimated_monthly_cost"] or 0)))
    return items[:limit]


def _compute_items(db: "Session", user: "User") -> list[dict]:
    """컴퓨트 리소스별 CPU 사용률을 조회한다(정렬·상한 없음) — `get_top_utilization()`과
    `get_unused_resources()`가 같은 조회를 공유한다."""
    rows = (
        db.query(Resource, CloudAccount, ServiceCatalog)
        .join(CloudAccount, Resource.cloud_account_id == CloudAccount.id)
        .join(ServiceCatalog, Resource.service_catalog_id == ServiceCatalog.id)
        .filter(
            CloudAccount.user_id == user.id,
            Resource.is_stale.is_(False),
            Resource.deleted_at.is_(None),
            ServiceCatalog.service_code.in_(_COMPUTE_SERVICE_CODES.keys()),
            # AWS의 "ec2" service_code는 EC2 인스턴스와 EBS Volume이 같이 쓴다
            # (resource_actions.py도 같은 구분을 한다) — 볼륨은 CPU가 없으니 여기서 제외한다.
            Resource.original_resource_type != "EBS Volume",
        )
        .all()
    )
    if not rows:
        return []

    # 계정(cloud_account)별로 묶어서 credential 복호화·CSP 호출을 계정당 1회로 줄인다.
    grouped: dict[int, list[tuple[Resource, CloudAccount, ServiceCatalog]]] = {}
    for row in rows:
        grouped.setdefault(row[1].id, []).append(row)

    # 같은 실제 리소스가 서로 다른 cloud_account에 중복 등록돼 있을 수 있다(같은 AWS 계정을
    # credential 여러 개로 등록하는 경우 등, 2026-09-19 실사용 중 발견 — DB에서 같은
    # external_resource_id를 가진 Resource 행 2개가 서로 다른 cloud_account_id로 존재함을
    # 직접 확인함). "인벤토리 조회"(resources 테이블)와 "메트릭 조회"(CSP 호출)를 합치는
    # 지점 자체의 키 불일치는 아니었다 — cpu_map은 external_resource_id로 정확히 조회된다.
    # 문제는 그 앞 단계, 같은 external_resource_id를 가진 Resource 행이 애초에 여러 개
    # 나온다는 것이었다. 그래서 최종 결과를 (provider, external_resource_id) 기준 dict에
    # 넣었다 빼서 자연스럽게 한 번만 남게 한다.
    items_by_key: dict[tuple[str, str], dict] = {}
    for account_id, group in grouped.items():
        account = group[0][1]
        cpu_map = _cpu_map_for_account(db, account, group)
        for resource, _account, service in group:
            key = (account.provider, resource.external_resource_id)
            if key in items_by_key:
                continue  # 이미 다른 cloud_account에서 같은 실제 리소스를 담았다
            items_by_key[key] = {
                "resource_id": str(resource.id),
                "provider": account.provider,
                "name": resource.name or resource.external_resource_id,
                "original_resource_type": resource.original_resource_type,
                "cpu_percent": cpu_map.get(resource.external_resource_id),
                "mem_percent": None,
            }

    return list(items_by_key.values())


def _cpu_map_for_account(
    db: "Session", account: CloudAccount, group: list[tuple[Resource, CloudAccount, ServiceCatalog]]
) -> dict[str, float | None]:
    credential = (
        db.query(Credential)
        .filter(Credential.cloud_account_id == account.id, Credential.verified.is_(True))
        .order_by(Credential.display_order, Credential.id)
        .first()
    )
    if credential is None:
        return {}

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
        secret_payload = resolve_secret_payload(account.provider, secret_payload, credential_id=credential.id)
    except (CredentialEncryptionError, CredentialResolutionError):
        return {}

    try:
        if account.provider == "aws":
            from app.providers import aws as aws_provider

            by_region: dict[str, list[str]] = {}
            for resource, _account, _service in group:
                by_region.setdefault(resource.region or "ap-northeast-2", []).append(resource.external_resource_id)
            out: dict[str, float | None] = {}
            for region, instance_ids in by_region.items():
                out.update(aws_provider.get_cpu_utilization(secret_payload, region, instance_ids))
            return out

        if account.provider == "azure":
            from app.providers import azure as azure_provider

            resource_ids = [resource.external_resource_id for resource, _account, _service in group]
            return azure_provider.get_cpu_utilization(secret_payload, resource_ids)

        if account.provider == "gcp":
            from app.providers import gcp as gcp_provider

            instance_names = [resource.external_resource_id for resource, _account, _service in group]
            return gcp_provider.get_cpu_utilization(secret_payload, account.external_account_id, instance_names)
    finally:
        del secret_payload

    return {}
