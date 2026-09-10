"""화면에 하드코딩돼 있던 예시 데이터를 DB에 그대로 심는 목업 시딩 스크립트.

이후 BE API(키 관리/리소스 조회/대시보드)가 이 DB만 조회해도 기존 화면(MY-01, INV-01,
DASH-01)과 값이 동일하게 나오도록 하는 것이 목적이다. 재실행해도 행이 중복되지 않는
idempotent 방식으로 작성한다(자연키 기준 get-or-create).

주의: credentials는 평문 저장하지 않는다. app.security.credential_crypto의
AES-256-GCM 유틸로 실제 암호화해 저장한다(가짜 값이라도 암/복호화 왕복이 되어야 한다).
"""

from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal
from typing import Any

import bcrypt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    CloudAccount,
    CloudResourceCost,
    Credential,
    Notification,
    ProvisioningJob,
    Resource,
    ResourceSyncJob,
    ResourceSyncJobItem,
    ServiceCatalog,
    User,
)
from app.security.credential_crypto import encrypt_credential_json
from app.seed import seed_service_catalog

# 시딩 값의 시점 기준(고정). 재실행 시에도 동일 값이 되도록 상수로 둔다.
_NOW = dt.datetime(2026, 9, 10, 9, 0, 0, tzinfo=dt.timezone.utc)
_TODAY = _NOW.date()
_MONTH_START = _TODAY.replace(day=1)
_MONTH_END = _TODAY.replace(day=calendar.monthrange(_TODAY.year, _TODAY.month)[1])

DEMO_USER_EMAIL = "demo@multicloud.example"
DEMO_PASSWORD = "demo-pass-1234"  # 데모 전용(실제 인증 단계 전까지의 자리표시값).


def get_or_create(db: Session, model, defaults: dict[str, Any] | None = None, **filters):
    """filters(자연키)로 조회해 있으면 그대로 반환, 없으면 defaults 병합해 생성.

    재실행 시 기존 행을 갱신하지 않는다(중복만 방지). 생성 시 flush로 id를 확정한다.
    """
    instance = db.query(model).filter_by(**filters).one_or_none()
    if instance is not None:
        return instance
    params = {**filters, **(defaults or {})}
    instance = model(**params)
    db.add(instance)
    db.flush()
    return instance


def _encrypt(payload: dict) -> dict[str, Any]:
    ciphertext, nonce = encrypt_credential_json(payload)
    return {
        "encrypted_payload": ciphertext,
        "encryption_nonce": nonce,
        "encryption_key_version": get_settings().credential_encryption_key_version,
    }


def seed_mock_data(db: Session) -> None:
    # 0) service_catalog 12행(이미 있으면 upsert). 리소스/프로비저닝이 이 카탈로그를 참조한다.
    seed_service_catalog(db)
    db.flush()

    catalog = {
        (c.provider, c.service_code): c
        for c in db.query(ServiceCatalog).all()
    }

    # 1) 데모 사용자 1명
    user = db.query(User).filter_by(normalized_email=DEMO_USER_EMAIL).one_or_none()
    if user is None:
        pw_hash = bcrypt.hashpw(DEMO_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        user = User(
            email=DEMO_USER_EMAIL,
            normalized_email=DEMO_USER_EMAIL,
            password_hash=pw_hash,
            name="데모 사용자",
            affiliation_type="individual",
            status="active",
        )
        db.add(user)
        db.flush()

    # 2) 클라우드 계정 3개 (MY-01 예시 라벨과 동일)
    acct_aws = get_or_create(
        db, CloudAccount,
        defaults={"account_label": "prod-aws-01"},
        user_id=user.id, provider="aws", external_account_id="111122223333",
    )
    acct_az = get_or_create(
        db, CloudAccount,
        defaults={"account_label": "prod-az-01"},
        user_id=user.id, provider="azure",
        external_account_id="00000000-1111-2222-3333-444455556666",
    )
    acct_gcp = get_or_create(
        db, CloudAccount,
        defaults={"account_label": "dev-gcp-01"},
        user_id=user.id, provider="gcp", external_account_id="dev-gcp-01-project",
    )

    # 3) 자격증명 3개 (계정당 1개). payload는 실제 암호화해 저장.
    #    prod-* 는 verified=true, dev-gcp-01 은 verified=false(MY-01 "검증 실패" 예시와 동일).
    cred_aws = get_or_create(
        db, Credential,
        defaults={
            **_encrypt({
                "access_key_id": "AKIAEXAMPLE0001",
                "secret_access_key": "wJalrXUtnFEMI/K7MDENG/EXAMPLEKEY",
            }),
            "public_identifier": "AKIA••••••••0001",
            "verified": True,
            "verified_at": _NOW,
        },
        cloud_account_id=acct_aws.id, name="prod-aws-01-key",
    )
    cred_az = get_or_create(
        db, Credential,
        defaults={
            **_encrypt({
                "client_id": "11111111-2222-3333-4444-555555555555",
                "client_secret": "az-sp-secret-example",
                "tenant_id": "99999999-8888-7777-6666-555555555555",
            }),
            "public_identifier": "sp:1111••••5555",
            "verified": True,
            "verified_at": _NOW,
        },
        cloud_account_id=acct_az.id, name="prod-az-01-sp",
    )
    cred_gcp = get_or_create(
        db, Credential,
        defaults={
            **_encrypt({
                "type": "service_account",
                "client_email": "sa-dev@dev-gcp-01-project.iam.gserviceaccount.com",
                "private_key_id": "abcdef0123456789",
                "private_key": "-----BEGIN PRIVATE KEY-----\\nEXAMPLE\\n-----END PRIVATE KEY-----\\n",
            }),
            "public_identifier": "sa-dev@dev-gcp-01-project…",
            "verified": False,  # MY-01 검증 실패 예시
        },
        cloud_account_id=acct_gcp.id, name="dev-gcp-01-sa",
    )

    # 4) 리소스 5개 (INV-01 예시). EBS Volume은 별도 카탈로그 서비스가 없어 aws/ec2에
    #    매핑하고 CSP 원본 유형만 original_resource_type으로 보존한다.
    resource_specs = [
        # (external_id, account, cred, (provider, service_code), original_type, region, status, env, cost)
        ("mcp-a1b2-vm", acct_aws, cred_aws, ("aws", "ec2"), "EC2 Instance",
         "ap-northeast-2", "RUNNING", "prod", (Decimal("128.40"), Decimal("481.50"))),
        ("mcp-c3d4-vm", acct_az, cred_az, ("azure", "vm"), "Virtual Machine",
         "koreacentral", "STOPPED", "prod", (Decimal("64.10"), Decimal("240.38"))),
        ("mcp-g7h8-vm", acct_gcp, cred_gcp, ("gcp", "compute_engine"), "Compute Engine Instance",
         "asia-northeast3", "RUNNING", "dev", None),
        ("mcp-a1b2-disk", acct_aws, cred_aws, ("aws", "ec2"), "EBS Volume",
         "ap-northeast-2", "AVAILABLE", "prod", None),
        ("mcp-e5f6-sql", acct_az, cred_az, ("azure", "sql_database"), "SQL Database",
         "koreacentral", "RUNNING", "prod", None),
    ]
    resources: dict[str, Resource] = {}
    for ext_id, acct, cred, svc_key, original_type, region, status, env, cost in resource_specs:
        svc = catalog[svc_key]
        provider_resource_key = f"{acct.provider}:{region}:{acct.external_account_id}:{ext_id}"
        defaults: dict[str, Any] = {
            "service_catalog_id": svc.id,
            "first_collected_by_credential_id": cred.id,
            "last_collected_by_credential_id": cred.id,
            "external_resource_id": ext_id,
            "original_resource_type": original_type,
            "name": ext_id,
            "region": region,
            "status": status,
            "tags": {"env": env},
            "first_seen_at": _NOW,
            "last_seen_at": _NOW,
            "last_synced_at": _NOW,
        }
        if cost is not None:
            collected, estimated = cost
            defaults.update({
                "collected_cost_amount": collected,
                "estimated_monthly_cost": estimated,
                "cost_currency": "USD",
                "cost_period_start": _MONTH_START,
                "cost_period_end": _MONTH_END,
                "cost_as_of": _NOW,
                "cost_source": "seed",
            })
        res = get_or_create(
            db, Resource, defaults=defaults,
            cloud_account_id=acct.id, provider_resource_key=provider_resource_key,
        )
        resources[ext_id] = res

    # 5) 프로비저닝 잡 (DASH-01 최근 활동 중 '생성' 계열만).
    #    중지 액션(mcp-a1b2-vm)은 resources/action 이력 성격이라 여기 넣지 않는다.
    job_created = get_or_create(
        db, ProvisioningJob,
        defaults={
            "credential_id": cred_az.id,
            "service_catalog_id": catalog[("azure", "vm")].id,
            "idempotency_key": "seed-prov-mcp-c3d4-vm",
            "spec_json": {"vm_size": "Standard_B2s", "region": "koreacentral"},
            "status": "success",
            "progress_percent": 100,
            "created_resource_count": 1,
            "started_at": _NOW,
            "finished_at": _NOW,
        },
        user_id=user.id, workspace_name="mcp-c3d4-vm",
    )
    job_failed = get_or_create(
        db, ProvisioningJob,
        defaults={
            "credential_id": cred_gcp.id,
            "service_catalog_id": catalog[("gcp", "compute_engine")].id,
            "idempotency_key": "seed-prov-mcp-g7h8-vm",
            "spec_json": {"machine_type": "e2-medium", "region": "asia-northeast3"},
            "status": "failed",
            "progress_percent": 0,
            "error_code": "QUOTA_EXCEEDED",
            "error_message": "리전 asia-northeast3의 CPU 할당량을 초과했습니다.",
            "started_at": _NOW,
            "finished_at": _NOW,
        },
        user_id=user.id, workspace_name="mcp-g7h8-vm",
    )

    # 6) 리소스 동기화 작업 1건 + 계정별 항목 (INV-01 배너: AWS success/Azure running/GCP pending)
    sync_job = get_or_create(
        db, ResourceSyncJob,
        defaults={"status": "running", "started_at": _NOW},
        user_id=user.id, requested_at=_NOW,
    )
    sync_item_specs = [
        (acct_aws, cred_aws, "aws", "success", {"resources_discovered": 2, "resources_updated": 2,
                                                "started_at": _NOW, "finished_at": _NOW}),
        (acct_az, cred_az, "azure", "running", {"started_at": _NOW}),
        (acct_gcp, cred_gcp, "gcp", "pending", {}),
    ]
    for acct, cred, provider, status, extra in sync_item_specs:
        get_or_create(
            db, ResourceSyncJobItem,
            defaults={"credential_id": cred.id, "provider": provider, "status": status, **extra},
            sync_job_id=sync_job.id, cloud_account_id=acct.id,
        )

    # 7) 알림 (프로비저닝 완료/실패)
    get_or_create(
        db, Notification,
        defaults={
            "message_key": "notif.provisioning.succeeded",
            "message_params": {"resource": "mcp-c3d4-vm", "provider": "azure"},
            "is_read": False,
        },
        user_id=user.id, type="provisioning_succeeded",
        reference_type="provisioning_job", reference_id=job_created.id,
    )
    get_or_create(
        db, Notification,
        defaults={
            "message_key": "notif.provisioning.failed",
            "message_params": {"resource": "mcp-g7h8-vm", "provider": "gcp", "reason": "할당량 초과"},
            "is_read": False,
        },
        user_id=user.id, type="provisioning_failed",
        reference_type="provisioning_job", reference_id=job_failed.id,
    )

    # 8) 비용 이력 (대시보드 카드 예시값 고정). 비용 계산 로직은 이번 범위 아님.
    #    AWS/Azure만 채우고 GCP는 데이터 없음.
    cost_specs = [
        (resources["mcp-a1b2-vm"], "aws", "actual", Decimal("128.40"), "seed-aws-actual"),
        (resources["mcp-a1b2-vm"], "aws", "estimated", Decimal("481.50"), "seed-aws-estimated"),
        (resources["mcp-c3d4-vm"], "azure", "actual", Decimal("64.10"), "seed-azure-actual"),
        (resources["mcp-c3d4-vm"], "azure", "estimated", Decimal("240.38"), "seed-azure-estimated"),
    ]
    for res, provider, cost_kind, amount, record_key in cost_specs:
        get_or_create(
            db, CloudResourceCost,
            defaults={
                "resource_id": res.id,
                "cost_kind": cost_kind,
                "amount": amount,
                "currency": "USD",
                "period_start": _MONTH_START,
                "period_end": _MONTH_END,
                "as_of": _NOW,
                "source": "seed",
                "metadata_json": {},
            },
            provider=provider, source_record_key=record_key,
        )


def main() -> None:
    from app.db import SessionLocal

    with SessionLocal() as db:
        seed_mock_data(db)
        db.commit()
    print("목업 데이터 시딩 완료(idempotent).")


if __name__ == "__main__":
    main()
