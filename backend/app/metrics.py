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

    items: list[dict] = []
    for account_id, group in grouped.items():
        account = group[0][1]
        cpu_map = _cpu_map_for_account(db, account, group)
        for resource, _account, service in group:
            items.append(
                {
                    "resource_id": str(resource.id),
                    "provider": account.provider,
                    "name": resource.name or resource.external_resource_id,
                    "original_resource_type": resource.original_resource_type,
                    "cpu_percent": cpu_map.get(resource.external_resource_id),
                    "mem_percent": None,
                }
            )

    # cpu_percent가 None인 항목(조회 실패)은 뒤로 — 정렬 자체가 실패를 "0%"로 오인하지 않게 한다.
    items.sort(key=lambda it: (it["cpu_percent"] is None, -(it["cpu_percent"] or 0)))
    return items[:limit]


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
