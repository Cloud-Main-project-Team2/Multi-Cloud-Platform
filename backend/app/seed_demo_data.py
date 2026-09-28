"""최종발표용 데모 계정(`demo@exam.com` / `demo1234`) 데이터를 **지우고 새로 만든다**.

    docker compose exec api python -m app.seed_demo_data

`seed_mock_data.py`(개발용 최소 픽스처, 날짜 고정)와 달리 **오늘(UTC) 기준 상대 날짜**로 만든다 —
비용 6개월치, 이달 예산 소진율, 최근 급증처럼 "지금"에 걸린 화면이 언제 실행해도 살아 있게 하기
위해서다. 실행할 때마다 데모 사용자의 데이터를 전부 지우고 다시 만들므로(사용자 행·로그인 세션은
유지), 발표 중 누가 리소스를 지우거나 예산을 바꿔도 이 스크립트 한 번으로 원래대로 돌아온다.

데모 계정에서 누르는 버튼(동기화·수집·시작/중지/삭제·프로비저닝)은 CSP를 부르지 않는다 —
`app/demo.py` 참고. 비용은 `app.demo.demo_cost_rows()`(날짜의 순수 함수)로 만들어서, 이후 자동
수집이 같은 함수로 매일 어제 비용을 이어 붙인다.

다른 사용자의 데이터는 건드리지 않는다: 삭제는 전부 데모 사용자 id로 범위를 건다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import bcrypt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.cost.budget import calendar_period
from app.cost.coverage import BASIS_COMPLETE_RANGE
from app.cost.notify import evaluate_budget_thresholds
from app.cost.review import evaluate_and_notify_account
from app.demo import DEMO_COST_SOURCE, DEMO_PASSWORD, DEMO_USER_EMAIL, DEMO_USER_NAME, PROFILES, demo_cost_rows
from app.models import (
    AuditEvent,
    CloudAccount,
    CloudAccountCost,
    CostIngestionRun,
    CostReviewItem,
    Credential,
    Notification,
    ProvisioningJob,
    ReportDeliverySetting,
    ReportGeneration,
    Resource,
    ResourceSyncJob,
    ResourceSyncJobItem,
    ServiceCatalog,
    Team,
    TeamBudget,
    User,
)
from app.report_cost import build_cost_snapshot
from app.security.credential_crypto import encrypt_credential_json
from app.seed import seed_service_catalog

HISTORY_DAYS = 190  # 반기 보고서(182일)와 월별 추이 6개월이 모두 채워지도록

_FULL_SCOPE = {"inventory_read": True, "cost_read": True, "resource_control": True, "provision": True}
# Azure·GCP는 실제 검증 코드도 inventory_read만 프로빙한다(CLAUDE.md "permission_scope 프로빙 범위 축소").
_INVENTORY_ONLY = {"inventory_read": True, "cost_read": False, "resource_control": False, "provision": False}


@dataclass
class AccountSpec:
    key: str
    provider: str
    external_account_id: str
    label: str
    team: str | None
    credentials: list[dict]
    resources: list[dict] = field(default_factory=list)


def _aws_key(n: int) -> dict:
    return {
        "payload": {"access_key_id": f"AKIADEMO000000000{n:03d}", "secret_access_key": "demo-secret-not-a-real-key"},
        "public_identifier": f"AKIA••••••••{n:04d}",
    }


ACCOUNTS: list[AccountSpec] = [
    AccountSpec(
        "a1", "aws", "111122223333", "prod-aws-01", "플랫폼팀",
        credentials=[{"name": "prod-aws-01-key", **_aws_key(1), "scope": _FULL_SCOPE, "verified": True}],
        resources=[
            dict(svc="ec2", ext="i-0a1b2c3d4e5f60001", type="AWS::EC2::Instance", name="web-prod-01",
                 region="ap-northeast-2", status="RUNNING", spec="t3.medium", tags={"env": "prod", "app": "web"}, monthly="30.37"),
            dict(svc="ec2", ext="i-0a1b2c3d4e5f60002", type="AWS::EC2::Instance", name="web-prod-02",
                 region="ap-northeast-2", status="RUNNING", spec="t3.medium", tags={"env": "prod", "app": "web"}, monthly="30.37"),
            dict(svc="ec2", ext="i-0a1b2c3d4e5f60003", type="AWS::EC2::Instance", name="batch-worker-01",
                 region="ap-northeast-2", status="STOPPED", spec="t3.large", tags={"env": "prod", "app": "batch"},
                 monthly="60.74", stopped_days=12),
            dict(svc="ec2", ext="vol-0f9e8d7c6b5a40001", type="EBS Volume", name="batch-worker-01-data",
                 region="ap-northeast-2", status="AVAILABLE", tags={"env": "prod"}, monthly="8.00"),
            dict(svc="rds", ext="orders-db", type="RDS Instance", name="orders-db",
                 region="ap-northeast-2", status="AVAILABLE", spec="db.t3.medium", tags={"env": "prod", "app": "orders"}, monthly="70.08"),
            dict(svc="s3", ext="mcp-demo-prod-assets", type="S3 Bucket", name="mcp-demo-prod-assets",
                 region=None, status="AVAILABLE", tags={"env": "prod"}),
            dict(svc="cloudfront", ext="E2DEMOPROD0001", type="CloudFront Distribution", name="prod-web-cdn",
                 region=None, status="DEPLOYED", tags={"env": "prod"}),
            # 동기화에서 더 이상 보이지 않는 리소스 예시 — 인벤토리 기본 목록에서는 숨겨진다.
            dict(svc="ec2", ext="i-0a1b2c3d4e5f60099", type="AWS::EC2::Instance", name="legacy-bastion",
                 region="ap-northeast-2", status="STOPPED", spec="t3.micro", tags={"env": "prod"}, stale=True),
        ],
    ),
    AccountSpec(
        "a2", "aws", "444455556666", "stg-aws-02", None,
        credentials=[{
            "name": "stg-aws-02-role",
            "payload": {
                "auth_type": "assume_role",
                "role_arn": "arn:aws:iam::444455556666:role/MultiCloudOpsDemo",
                "external_id": "demo-external-id-0001",
            },
            "public_identifier": "arn:aws:iam::444455556666:role/MultiCloudOps…",
            "tags": {"auth_type": "assume_role"},
            "scope": _FULL_SCOPE, "verified": True,
        }],
        resources=[
            dict(svc="ec2", ext="i-0b2c3d4e5f6a70001", type="AWS::EC2::Instance", name="api-stg-01",
                 region="us-east-1", status="RUNNING", spec="t3.small", tags={"env": "stg", "app": "api"}, monthly="15.18"),
            dict(svc="rds", ext="api-stg-db", type="RDS Instance", name="api-stg-db",
                 region="us-east-1", status="STOPPED", spec="db.t3.micro", tags={"env": "stg"}, monthly="12.41", stopped_days=5),
            dict(svc="s3", ext="mcp-demo-stg-logs", type="S3 Bucket", name="mcp-demo-stg-logs",
                 region=None, status="AVAILABLE", tags={"env": "stg"}),
            dict(svc="cloudfront", ext="E2DEMOSTG00001", type="CloudFront Distribution", name="stg-web-cdn",
                 region=None, status="DEPLOYED", tags={"env": "stg"}),
        ],
    ),
    AccountSpec(
        "a3", "azure", "00000000-1111-2222-3333-444455556666", "prod-az-01", "플랫폼팀",
        credentials=[{
            "name": "prod-az-01-sp",
            "payload": {"client_id": "11111111-2222-3333-4444-555555555555", "client_secret": "demo-secret-not-a-real-key",
                        "tenant_id": "99999999-8888-7777-6666-555555555555"},
            "public_identifier": "sp:1111••••5555", "scope": _INVENTORY_ONLY, "verified": True,
        }],
        resources=[
            dict(svc="vm", ext="vm-prod-web-01", type="Microsoft.Compute/virtualMachines", name="vm-prod-web-01",
                 region="koreacentral", status="RUNNING", spec="Standard_B2s", tags={"env": "prod", "app": "web"}, monthly="35.04", arm=True),
            dict(svc="vm", ext="vm-prod-web-02", type="Microsoft.Compute/virtualMachines", name="vm-prod-web-02",
                 region="koreacentral", status="STOPPED", spec="Standard_B2s", tags={"env": "prod", "app": "web"},
                 monthly="35.04", arm=True, stopped_days=20),
            dict(svc="sql_database", ext="sqlsrv-prod-orders", type="Azure SQL Database", name="sqlsrv-prod-orders",
                 region="koreacentral", status="AVAILABLE", tags={"env": "prod"}, monthly="55.00"),
            dict(svc="storage_account", ext="stdemoprodassets01", type="Microsoft.Storage/storageAccounts", name="stdemoprodassets01",
                 region="koreacentral", status="AVAILABLE", tags={"env": "prod"}),
            dict(svc="cdn", ext="fd-demo-prod", type="Microsoft.Cdn/profiles", name="fd-demo-prod",
                 region=None, status="DEPLOYED", tags={"env": "prod"}),
        ],
    ),
    AccountSpec(
        "a4", "azure", "77777777-8888-9999-aaaa-bbbbccccdddd", "dev-az-02", "개발팀",
        credentials=[{
            "name": "dev-az-02-sp",
            "payload": {"client_id": "22222222-3333-4444-5555-666666666666", "client_secret": "demo-secret-not-a-real-key",
                        "tenant_id": "99999999-8888-7777-6666-555555555555"},
            "public_identifier": "sp:2222••••6666", "scope": _INVENTORY_ONLY, "verified": True,
        }],
        resources=[
            dict(svc="vm", ext="vm-dev-api-01", type="Microsoft.Compute/virtualMachines", name="vm-dev-api-01",
                 region="koreacentral", status="RUNNING", spec="Standard_B1s", tags={"env": "dev", "app": "api"}, monthly="10.51", arm=True),
            dict(svc="sql_database", ext="pg-demo-dev-01", type="Azure Database for PostgreSQL", name="pg-demo-dev-01",
                 region="koreacentral", status="AVAILABLE", tags={"env": "dev"}),
            dict(svc="storage_account", ext="stdemodevshared01", type="Microsoft.Storage/storageAccounts", name="stdemodevshared01",
                 region="koreacentral", status="AVAILABLE", tags={"env": "dev"}),
        ],
    ),
    AccountSpec(
        "a5", "gcp", "dev-gcp-01-project", "dev-gcp-01", "데이터팀",
        credentials=[
            {"name": "dev-gcp-01-sa", "payload": None, "sa_email": "sa-dev@dev-gcp-01-project.iam.gserviceaccount.com",
             "public_identifier": "sa-dev@dev-gcp-01-project…",
             "scope": _INVENTORY_ONLY, "verified": True},
            # 마이페이지 "검증 실패" 행 예시 — 같은 계정의 예전 키. 계정은 위의 검증된 키로 동작한다.
            {"name": "dev-gcp-01-sa-old", "payload": None, "sa_email": "sa-old@dev-gcp-01-project.iam.gserviceaccount.com",
             "public_identifier": "sa-old@dev-gcp-01-project…",
             "scope": {}, "verified": False},
        ],
        resources=[
            dict(svc="compute_engine", ext="gce-dev-01", type="Compute Engine Instance", name="gce-dev-01",
                 region="asia-northeast3-a", status="RUNNING", spec="e2-medium", tags={"env": "dev"}, monthly="30.66"),
            dict(svc="compute_engine", ext="gce-dev-02", type="Compute Engine Instance", name="gce-dev-02",
                 region="asia-northeast3-a", status="STOPPED", spec="e2-micro", tags={"env": "dev"}, monthly="7.67", stopped_days=8),
            dict(svc="cloud_storage", ext="mcp-demo-dev-assets", type="Cloud Storage Bucket", name="mcp-demo-dev-assets",
                 region="asia-northeast3", status="AVAILABLE", tags={"env": "dev"}),
        ],
    ),
    AccountSpec(
        "a6", "gcp", "analytics-gcp-02", "analytics-gcp-02", "데이터팀",
        credentials=[{"name": "analytics-gcp-02-sa", "payload": None,
                      "sa_email": "sa-etl@analytics-gcp-02.iam.gserviceaccount.com", "public_identifier": "sa-etl@analytics-gcp-02…",
                      "scope": _INVENTORY_ONLY, "verified": True}],
        resources=[
            dict(svc="compute_engine", ext="etl-runner-01", type="Compute Engine Instance", name="etl-runner-01",
                 region="us-central1-a", status="RUNNING", spec="e2-standard-4", tags={"env": "prod", "app": "etl"}, monthly="97.83"),
            dict(svc="cloud_sql", ext="analytics-pg", type="Cloud SQL Instance", name="analytics-pg",
                 region="us-central1", status="RUNNING", tags={"env": "prod"}, monthly="51.10"),
            dict(svc="cloud_storage", ext="mcp-demo-analytics-lake", type="Cloud Storage Bucket", name="mcp-demo-analytics-lake",
                 region="us-central1", status="AVAILABLE", tags={"env": "prod"}),
            dict(svc="cloud_cdn", ext="analytics-dash-fr", type="Cloud CDN (HTTP LB)", name="analytics-dash-fr",
                 region=None, status="DEPLOYED", tags={"env": "prod"}),
        ],
    ),
]

TEAMS = {"플랫폼팀": "USD", "데이터팀": "USD", "개발팀": "KRW"}


def _gcp_payload(project: str, email: str) -> dict:
    # 실제 서비스 계정 키 JSON과 같은 모양(token_uri 포함) — 값은 가짜다.
    return {
        "type": "service_account", "project_id": project, "private_key_id": "demo0000000000000000",
        "private_key": "-----BEGIN PRIVATE KEY-----\\nDEMO-NOT-A-REAL-KEY\\n-----END PRIVATE KEY-----\\n",
        "client_email": email, "client_id": "100000000000000000001",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
    }


def _at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=dt.timezone.utc)


def _money(value: Decimal, currency: str) -> Decimal:
    step = Decimal("1000") if currency == "KRW" else Decimal("10")
    return (value / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


# --- 1) 초기화 ------------------------------------------------------------------------------------


def _get_or_create_user(db: Session) -> User:
    user = db.query(User).filter_by(normalized_email=DEMO_USER_EMAIL).one_or_none()
    pw_hash = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode()
    if user is None:
        user = User(email=DEMO_USER_EMAIL, normalized_email=DEMO_USER_EMAIL, password_hash=pw_hash,
                    name=DEMO_USER_NAME, affiliation_type="company", affiliation_name="MultiCloud Ops 데모", status="active")
        db.add(user)
        db.flush()
    else:
        user.password_hash = pw_hash
        user.name = DEMO_USER_NAME
        user.affiliation_type = "company"
        user.affiliation_name = "MultiCloud Ops 데모"
        user.status = "active"
    return user


def _wipe(db: Session, user_id: int) -> None:
    account_ids = [a for (a,) in db.query(CloudAccount.id).filter(CloudAccount.user_id == user_id)]
    sync_ids = [s for (s,) in db.query(ResourceSyncJob.id).filter(ResourceSyncJob.user_id == user_id)]
    if sync_ids:
        db.query(ResourceSyncJobItem).filter(ResourceSyncJobItem.sync_job_id.in_(sync_ids)).delete(synchronize_session=False)
    for model in (ResourceSyncJob, ProvisioningJob, Notification, CostReviewItem, CostIngestionRun,
                  ReportGeneration, ReportDeliverySetting):
        db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.actor_user_id == user_id).delete(synchronize_session=False)
    if account_ids:
        # credentials·resources·비용 행은 cloud_accounts ON DELETE CASCADE로 함께 지워진다.
        db.query(CloudAccount).filter(CloudAccount.id.in_(account_ids)).delete(synchronize_session=False)
    db.query(Team).filter(Team.user_id == user_id).delete(synchronize_session=False)  # 예산·임계 기록 CASCADE
    db.flush()


# --- 2) 계정·키·리소스 ------------------------------------------------------------------------------


def _encrypted(payload: dict) -> dict[str, Any]:
    ciphertext, nonce = encrypt_credential_json(payload)
    return {"encrypted_payload": ciphertext, "encryption_nonce": nonce,
            "encryption_key_version": get_settings().credential_encryption_key_version}


def _create_accounts(db: Session, user: User, teams: dict[str, Team], now: dt.datetime, today: dt.date) -> dict[str, dict]:
    catalog = {(c.provider, c.service_code): c for c in db.query(ServiceCatalog).all()}
    month_start = today.replace(day=1)
    out: dict[str, dict] = {}
    for spec in ACCOUNTS:
        account = CloudAccount(user_id=user.id, provider=spec.provider, external_account_id=spec.external_account_id,
                               account_label=spec.label, team_id=teams[spec.team].id if spec.team else None)
        db.add(account)
        db.flush()
        creds: list[Credential] = []
        for order, c in enumerate(spec.credentials):
            payload = c["payload"] or _gcp_payload(spec.external_account_id, c["sa_email"])
            cred = Credential(
                cloud_account_id=account.id, name=c["name"], public_identifier=c["public_identifier"],
                permission_scope=c["scope"], verified=c["verified"],
                verified_at=now - dt.timedelta(days=6) if c["verified"] else None,
                tags=c.get("tags", {}), display_order=order, **_encrypted(payload),
            )
            db.add(cred)
            creds.append(cred)
        db.flush()
        cred = creds[0]
        currency = PROFILES[spec.external_account_id].currency
        resources: dict[str, Resource] = {}
        for i, r in enumerate(spec.resources):
            ext = r["ext"]
            if r.get("arm"):
                ext = (f"/subscriptions/{spec.external_account_id}/resourceGroups/rg-demo-{spec.label}"
                       f"/providers/Microsoft.Compute/virtualMachines/{r['ext']}")
            svc = catalog[(spec.provider, r["svc"])]
            first_seen = now - dt.timedelta(days=150 - i * 9)
            res = Resource(
                cloud_account_id=account.id, service_catalog_id=svc.id,
                first_collected_by_credential_id=cred.id, last_collected_by_credential_id=cred.id,
                provider_resource_key=f"{spec.provider}:{r['svc']}:{ext}",
                external_resource_id=ext, original_resource_type=r["type"], name=r["name"],
                region=r["region"], status=r["status"], tags={**r["tags"], "owner": "demo"},
                raw_metadata={"instance_type": r["spec"]} if r.get("spec") else None,
                first_seen_at=first_seen,
                last_seen_at=now - dt.timedelta(days=9) if r.get("stale") else now - dt.timedelta(hours=2),
                last_synced_at=now - dt.timedelta(hours=2),
                is_stale=bool(r.get("stale")),
                status_changed_at=now - dt.timedelta(days=r["stopped_days"]) if r.get("stopped_days") else first_seen,
            )
            if r.get("monthly"):
                monthly = Decimal(r["monthly"]) * (Decimal("1350") if currency == "KRW" else Decimal("1"))
                elapsed = max((today - month_start).days, 0)
                running_share = Decimal("0.35") if r["status"] == "STOPPED" else Decimal("1")
                res.estimated_monthly_cost = monthly
                res.collected_cost_amount = (monthly * elapsed / 30 * running_share).quantize(Decimal("0.01"))
                res.cost_currency = currency
                res.cost_period_start = month_start
                res.cost_period_end = today
                res.cost_as_of = now - dt.timedelta(minutes=40)
                res.cost_source = DEMO_COST_SOURCE
            db.add(res)
            resources[r["name"]] = res
        db.flush()
        out[spec.key] = {"account": account, "credential": cred, "resources": resources, "spec": spec}
    return out


# --- 3) 비용 + 수집 이력 ---------------------------------------------------------------------------


def _seed_costs(db: Session, user: User, accounts: dict[str, dict], now: dt.datetime, today: dt.date) -> None:
    start = today - dt.timedelta(days=HISTORY_DAYS)
    backfill_end = today - dt.timedelta(days=14)
    as_of = now - dt.timedelta(minutes=40)
    for entry in accounts.values():
        account: CloudAccount = entry["account"]
        rows = demo_cost_rows(account.external_account_id, start, today)
        db.bulk_insert_mappings(CloudAccountCost, [
            dict(cloud_account_id=account.id, provider=account.provider, cost_kind="actual",
                 charge_category=r.charge_category, service=r.service, amount=r.amount, currency=r.currency,
                 period_start=r.period_start, period_end=r.period_end, is_estimated=False, as_of=as_of,
                 source=DEMO_COST_SOURCE, source_record_key=r.source_record_key, tags={}, metadata_json=None)
            for r in rows
        ])

        def run(trigger: str, p_start: dt.date, p_end: dt.date, finished: dt.datetime, status: str = "success",
                error_code: str | None = None, error_message: str | None = None) -> None:
            replaced = sum(1 for r in rows if p_start <= r.period_start < p_end) if status == "success" else 0
            db.add(CostIngestionRun(
                user_id=user.id, cloud_account_id=account.id, trigger_type=trigger, status=status,
                period_start=p_start, period_end=p_end, requested_at=finished - dt.timedelta(minutes=2),
                started_at=finished - dt.timedelta(minutes=2), finished_at=finished,
                api_calls=0 if status != "success" else 3, records_replaced=replaced,
                error_code=error_code, error_message=error_message,
                coverage_basis=BASIS_COMPLETE_RANGE if status == "success" else None,
            ))

        # 계정 연결 직후의 과거 구간 일괄 수집 → 이후 매일 자동 수집(직전 7일 재수집).
        run("manual", start, backfill_end, _at(backfill_end, 7, 10))
        for back in range(13, 0, -1):
            day = today - dt.timedelta(days=back)
            if back == 9 and account.provider == "aws":
                run("auto", day - dt.timedelta(days=7), day, _at(day, 6, 2), status="failed",
                    error_code="PROVIDER_RATE_LIMITED", error_message="Cost Explorer 요청 한도를 초과했습니다. 다음 수집에서 다시 시도합니다.")
            run("auto", day - dt.timedelta(days=7), day, _at(day, 6, 3))
        run("auto", today - dt.timedelta(days=7), today, min(_at(today, 6, 3), now - dt.timedelta(minutes=40)))
    db.flush()


# --- 4) 팀·예산 → 임계 알림 -------------------------------------------------------------------------


def _team_mtd(db: Session, team: Team, start: dt.date, end: dt.date) -> Decimal:
    total = (
        db.query(func.coalesce(func.sum(CloudAccountCost.amount), 0))
        .join(CloudAccount, CloudAccount.id == CloudAccountCost.cloud_account_id)
        .filter(CloudAccount.team_id == team.id, CloudAccountCost.charge_category == "usage",
                CloudAccountCost.period_start >= start, CloudAccountCost.period_start < end)
        .scalar()
    )
    return Decimal(total)


def _seed_budgets(db: Session, teams: dict[str, Team], now: dt.datetime, today: dt.date) -> None:
    month_start, _ = calendar_period("monthly", today)
    quarter_start, _ = calendar_period("quarterly", today)

    def limit_for(team: Team, start: dt.date, target_ratio: str, fallback_days: int) -> Decimal:
        spent = _team_mtd(db, team, start, today)
        if spent <= 0:  # 기간 첫날 — 직전 N일 평균으로 한 기간치를 잡는다
            spent = _team_mtd(db, team, today - dt.timedelta(days=fallback_days), today)
            return max(_money(spent, team.currency), Decimal("10"))
        return max(_money(spent / Decimal(target_ratio), team.currency), Decimal("10"))

    platform, data, dev = teams["플랫폼팀"], teams["데이터팀"], teams["개발팀"]
    # 플랫폼팀: 석 달 전부터 월 예산 → 이달 한도를 올린 새 행(이력 보존). 이달 소진율 약 86% → 80% 알림.
    prev_start = (month_start - dt.timedelta(days=80)).replace(day=1)
    db.add(TeamBudget(team_id=platform.id, period_type="monthly", start_date=prev_start,
                      limit_amount=Decimal("1500"), currency="USD"))
    db.add(TeamBudget(team_id=platform.id, period_type="monthly", start_date=month_start,
                      limit_amount=limit_for(platform, month_start, "0.86", 30), currency="USD"))
    # 연말 캠페인용 기간 예산(예정) — 반복 예산과 별개로 기간을 덮어쓰는 custom 예산 예시.
    db.add(TeamBudget(team_id=platform.id, period_type="custom", start_date=today + dt.timedelta(days=30),
                      end_date=today + dt.timedelta(days=45), limit_amount=Decimal("900"), currency="USD"))
    # 데이터팀: 이달 이미 한도 초과(약 107%) → 80%·100% 알림 둘 다.
    db.add(TeamBudget(team_id=data.id, period_type="monthly", start_date=(month_start - dt.timedelta(days=40)).replace(day=1),
                      limit_amount=limit_for(data, month_start, "1.07", 30), currency="USD"))
    # 개발팀(원화): 분기 예산, 여유 있음(약 45%).
    db.add(TeamBudget(team_id=dev.id, period_type="quarterly", start_date=quarter_start,
                      limit_amount=limit_for(dev, quarter_start, "0.45", 90), currency="KRW"))
    db.flush()

    for team, ages in ((platform, [dt.timedelta(days=2)]), (data, [dt.timedelta(days=6), dt.timedelta(hours=20)])):
        created = evaluate_budget_thresholds(db, team, today)
        for notif, age in zip(created, ages):
            notif.created_at = now - age
            notif.is_read = age > dt.timedelta(days=3)
            notif.read_at = notif.created_at + dt.timedelta(hours=1) if notif.is_read else None
    db.flush()


# --- 5) 급증 탐지 → 검토 큐 ------------------------------------------------------------------------


_RESOLVED_NOTES = [
    ("expected", "예정된 작업(배치·이벤트)으로 사용량이 늘어난 날입니다. 정상 패턴으로 종결."),
    ("too_small", "금액이 작아 조치 불필요로 종결."),
    ("unexpected", "의도하지 않은 사용 — 담당자에게 공유하고 설정을 되돌렸습니다."),
]
_INVESTIGATING_NOTE = "원인 확인 중 — 해당 서비스 담당자에게 사용 내역을 요청했습니다."


def _seed_reviews(db: Session, accounts: dict[str, dict], now: dt.datetime, today: dt.date) -> None:
    items: list[CostReviewItem] = []
    for entry in accounts.values():
        items.extend(evaluate_and_notify_account(db, entry["account"], today))
    db.flush()
    notifs = {
        n.reference_id: n for n in db.query(Notification).filter(
            Notification.reference_type == "cost_review_item", Notification.reference_id.in_([i.id for i in items] or [-1]))
    }

    def detected_on(item: CostReviewItem) -> dt.datetime:
        day = dt.date.fromisoformat(item.source_key.rsplit(":", 1)[1])
        return min(_at(day + dt.timedelta(days=4), 6, 5), now - dt.timedelta(hours=1))

    items.sort(key=detected_on)
    # 최근 2주에 잡힌 것은 아직 처리 전(open), 그중 두 번째로 새것은 조사 중, 그 이전은 처리 완료로 둔다.
    recent = [i for i in items if detected_on(i) >= now - dt.timedelta(days=14)]
    for idx, item in enumerate(items):
        when = detected_on(item)
        item.created_at = when
        if item in recent:
            if len(recent) >= 2 and item is recent[-2]:
                item.status, item.note = "investigating", _INVESTIGATING_NOTE
        else:
            item.status = "resolved"
            item.resolution, item.note = _RESOLVED_NOTES[idx % len(_RESOLVED_NOTES)]
            item.resolved_at = when + dt.timedelta(days=1)
        n = notifs.get(item.id)
        if n is not None:
            n.created_at = when
            n.is_read = item.status != "open"
            n.read_at = when + dt.timedelta(hours=3) if n.is_read else None
    db.flush()


# --- 6) 프로비저닝·동기화 이력 ----------------------------------------------------------------------


def _seed_jobs(db: Session, user: User, accounts: dict[str, dict], now: dt.datetime) -> None:
    catalog = {(c.provider, c.service_code): c for c in db.query(ServiceCatalog).all()}
    jobs = [
        # (계정, 서비스, 이름, 며칠 전, 상태, error_code, error_message, 소요 분, 추가 spec)
        ("a1", "ec2", "web-prod-02", 20, "success", None, None, 3, {"instance_type": "t3.medium", "region": "ap-northeast-2"}),
        ("a3", "vm", "vm-prod-web-02", 18, "success", None, None, 5, {"instance_type": "Standard_B2s", "region": "koreacentral"}),
        ("a6", "cloud_sql", "analytics-pg", 16, "success", None, None, 11, {"engine": "PostgreSQL", "region": "us-central1"}),
        ("a5", "compute_engine", "gce-dev-03", 14, "failed", "QUOTA_EXCEEDED",
         "리전 asia-northeast3의 CPU 할당량을 초과했습니다. 할당량 증설을 요청하거나 다른 리전을 선택하세요.", 1,
         {"instance_type": "e2-standard-4", "region": "asia-northeast3"}),
        ("a2", "s3", "mcp-demo-stg-logs", 12, "success", None, None, 1, {"region": "us-east-1"}),
        ("a4", "vm", "vm-dev-api-02", 10, "failed", "TERRAFORM_ERROR",
         "Standard_B1s 크기는 현재 koreacentral 리전에서 생성할 수 없습니다(SkuNotAvailable). 다른 크기나 리전을 선택하세요.", 2,
         {"instance_type": "Standard_B1s", "region": "koreacentral"}),
        ("a1", "rds", "orders-db-replica", 8, "failed", "VALIDATION_ERROR",
         "master_username에 예약어(admin)는 사용할 수 없습니다.", 0, {"engine": "MySQL", "region": "ap-northeast-2"}),
        ("a6", "cloud_storage", "mcp-demo-analytics-lake", 7, "success", None, None, 1, {"region": "us-central1"}),
        ("a3", "cdn", "fd-demo-prod", 6, "success", None, None, 14, {"sku": "Standard_AzureFrontDoor"}),
        ("a5", "cloud_sql", "dev-mysql-01", 4, "cancelled", None, None, 2, {"engine": "MySQL", "region": "asia-northeast3"}),
        ("a2", "ec2", "api-stg-01", 3, "success", None, None, 3, {"instance_type": "t3.small", "region": "us-east-1"}),
        ("a4", "storage_account", "stdemodevshared01", 2, "success", None, None, 1, {"region": "koreacentral"}),
        ("a2", "cloudfront", "stg-web-cdn", 1, "success", None, None, 9, {"origin": "mcp-demo-stg-logs.s3.amazonaws.com"}),
    ]
    for n, (key, svc, name, days_ago, status, code, message, minutes, pspec) in enumerate(jobs, start=1):
        entry = accounts[key]
        account, cred = entry["account"], entry["credential"]
        requested = now - dt.timedelta(days=days_ago, hours=(n * 5) % 9)
        rejected = code == "VALIDATION_ERROR"
        resource = entry["resources"].get(name)
        job = ProvisioningJob(
            user_id=user.id, credential_id=cred.id, service_catalog_id=catalog[(account.provider, svc)].id,
            workspace_name=f"pending-demo-{n}" if rejected else f"user-{user.id}-job-demo-{n}",
            idempotency_key=f"demo-seed-{n}",
            spec_json={"common_spec": {"name": name, "tags": {"env": "demo"}}, "provider_spec": pspec},
            status=status, progress_percent=100 if status == "success" else (35 if status == "cancelled" else 0),
            created_resource_count=1 if status == "success" else 0,
            result_json={"resource_name": name} if status == "success" else None,
            error_code=code, error_message=message,
            started_at=None if rejected else requested + dt.timedelta(seconds=5),
            finished_at=requested + dt.timedelta(minutes=minutes, seconds=20),
        )
        job.created_at = requested
        db.add(job)
        db.flush()
        if status in ("success", "failed") and not rejected:
            params: dict[str, Any] = {"resource": name, "job_id": str(job.id)}
            if message:
                params["reason"] = message
            notif = Notification(
                user_id=user.id, type=f"provisioning_{'succeeded' if status == 'success' else 'failed'}",
                reference_type="provisioning_job", reference_id=job.id,
                message_key=f"notif.provisioning.{'succeeded' if status == 'success' else 'failed'}",
                message_params=params, is_read=days_ago > 2,
            )
            notif.created_at = job.finished_at
            notif.read_at = job.finished_at + dt.timedelta(hours=2) if notif.is_read else None
            db.add(notif)
        if resource is not None and status == "success":
            resource.first_seen_at = min(resource.first_seen_at, job.finished_at)

    # 동기화: 최근 1주 5회. 3일 전 한 번은 Azure 한 계정이 일시 오류로 부분 성공.
    counts = {k: len([r for r in e["resources"].values() if not r.is_stale]) for k, e in accounts.items()}
    for days_ago, hours, partial in ((7, 3, False), (5, 9, False), (3, 2, True), (1, 8, False), (0, 2, False)):
        requested = now - dt.timedelta(days=days_ago, hours=hours)
        job = ResourceSyncJob(user_id=user.id, status="partial_success" if partial else "success",
                              requested_at=requested, started_at=requested + dt.timedelta(seconds=2),
                              finished_at=requested + dt.timedelta(seconds=48))
        job.created_at = requested
        db.add(job)
        db.flush()
        for key, entry in accounts.items():
            failed = partial and key == "a4"
            db.add(ResourceSyncJobItem(
                sync_job_id=job.id, cloud_account_id=entry["account"].id, credential_id=entry["credential"].id,
                provider=entry["account"].provider, status="failed" if failed else "success",
                resources_discovered=0 if failed else counts[key], resources_updated=0 if failed else counts[key],
                error_code="PROVIDER_API_ERROR" if failed else None,
                error_message="Azure Resource Manager 응답 지연으로 목록을 가져오지 못했습니다." if failed else None,
                started_at=requested + dt.timedelta(seconds=3), finished_at=requested + dt.timedelta(seconds=40),
            ))
        if days_ago <= 1:
            notif = Notification(
                user_id=user.id, type="resource_sync_succeeded", reference_type="resource_sync_job", reference_id=job.id,
                message_key="notif.resource_sync.succeeded",
                message_params={"job_id": str(job.id), "discovered": sum(counts.values()), "created": 0,
                                "updated": sum(counts.values())},
                is_read=days_ago == 1,
            )
            notif.created_at = job.finished_at
            db.add(notif)
    db.flush()


# --- 7) 보고서 생성 이력 ----------------------------------------------------------------------------


def _seed_reports(db: Session, user: User, now: dt.datetime, today: dt.date) -> None:
    last_month_end = today.replace(day=1) - dt.timedelta(days=1)
    reports = [
        ("MONTHLY", last_month_end.replace(day=1), last_month_end, ["aws", "azure", "gcp"], dt.timedelta(days=today.day - 1, hours=-9)),
        ("WEEKLY", today - dt.timedelta(days=7), today - dt.timedelta(days=1), ["aws", "azure", "gcp"], dt.timedelta(hours=5)),
        ("HALF_YEARLY", today - dt.timedelta(days=182), today - dt.timedelta(days=1), ["aws", "gcp"], dt.timedelta(hours=3)),
    ]
    for period_type, p_from, p_to, clouds, age in reports:
        db.add(ReportGeneration(
            user_id=user.id, period_type=period_type, period_from=p_from, period_to=p_to,
            providers=",".join(sorted(clouds)), generated_at=now - age,
            cost_snapshot=build_cost_snapshot(db, user, p_from, p_to, clouds),
            ai_summary=None,  # OpenAI 호출은 화면에서 "생성하기"를 다시 누를 때 한다
        ))
    db.add(ReportDeliverySetting(user_id=user.id, delivery_method="WEB", period_type="WEEKLY"))
    db.flush()


# --- 진입점 ----------------------------------------------------------------------------------------


def seed_demo_data(db: Session, *, now: dt.datetime | None = None) -> User:
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(dt.timezone.utc).date()  # 비용 쪽 날짜 경계는 UTC(coverage.utc_today와 같은 값)
    seed_service_catalog(db)
    db.flush()

    user = _get_or_create_user(db)
    _wipe(db, user.id)

    teams = {name: Team(user_id=user.id, name=name, currency=cur) for name, cur in TEAMS.items()}
    db.add_all(teams.values())
    db.flush()

    accounts = _create_accounts(db, user, teams, now, today)
    _seed_costs(db, user, accounts, now, today)
    _seed_budgets(db, teams, now, today)
    _seed_reviews(db, accounts, now, today)
    _seed_jobs(db, user, accounts, now)
    _seed_reports(db, user, now, today)
    return user


def main() -> None:
    from app.db import SessionLocal

    with SessionLocal() as db:
        user = seed_demo_data(db)
        db.commit()
        print(f"데모 데이터 시딩 완료 — {DEMO_USER_EMAIL} / {DEMO_PASSWORD} (user_id={user.id})")


if __name__ == "__main__":
    main()
