"""최종발표 데모 계정 — 시드 결과와 "CSP를 부르지 않는" 가드 검증(app/demo.py, app/seed_demo_data.py)."""

import datetime as dt
from decimal import Decimal

import pytest

import app.routers.resources as resources_router
import app.routers.sync_jobs as sync_router
from app.cost.budget import compute_budget_status
from app.cost.coverage import utc_today
from app.cost.query import CostQuery, forecast_month_end
from app.demo import DEMO_COST_SOURCE, DEMO_USER_EMAIL, DemoCostProvider, demo_cost_rows, fake_terraform_outputs
from app.models import (
    CloudAccount,
    CloudAccountCost,
    CostReviewItem,
    Credential,
    Notification,
    ProvisioningJob,
    Resource,
    ResourceSyncJob,
    ResourceSyncJobItem,
    Team,
    User,
)
from app.routers.provisioning import _resource_attrs
from app.seed_demo_data import seed_demo_data


@pytest.fixture()
def demo_user(db_session):
    return seed_demo_data(db_session)


def _accounts(db, user):
    return {a.account_label: a for a in db.query(CloudAccount).filter_by(user_id=user.id)}


def test_seed_builds_every_screen(db_session, demo_user):
    db = db_session
    accounts = _accounts(db, demo_user)
    assert len(accounts) == 6
    assert {a.provider for a in accounts.values()} == {"aws", "azure", "gcp"}
    assert db.query(Resource).join(CloudAccount).filter(CloudAccount.user_id == demo_user.id).count() >= 20
    assert db.query(ProvisioningJob).filter_by(user_id=demo_user.id).count() >= 10
    assert db.query(CostReviewItem).filter_by(user_id=demo_user.id).count() >= 3
    types = {n.type for n in db.query(Notification).filter_by(user_id=demo_user.id)}
    assert {"budget_threshold", "cost_anomaly", "provisioning_succeeded", "provisioning_failed"} <= types
    # 원화 청구 계정이 섞여 있다(통화별 표·카드 예시).
    currencies = {c for (c,) in db.query(CloudAccountCost.currency).filter(
        CloudAccountCost.cloud_account_id.in_([a.id for a in accounts.values()])).distinct()}
    assert currencies == {"USD", "KRW"}


def test_seed_is_rerunnable_and_resets(db_session, demo_user):
    db = db_session
    web = db.query(Resource).filter_by(name="web-prod-01").one()
    web.deleted_at = dt.datetime.now(dt.timezone.utc)  # 발표 중 누가 지웠다
    db.flush()
    count = db.query(CloudAccountCost).count()

    seed_demo_data(db)

    assert db.query(User).filter_by(normalized_email=DEMO_USER_EMAIL).count() == 1
    assert db.query(Resource).filter_by(name="web-prod-01", deleted_at=None).count() == 1
    assert db.query(CloudAccountCost).count() == count


def test_seeded_costs_are_analysis_ready(db_session, demo_user):
    today = utc_today()
    if today.day == 1:
        pytest.skip("월 첫날은 전망을 내지 않는다(first_day)")
    q = CostQuery(period_start=today.replace(day=1), period_end=today)
    rows, status = forecast_month_end(db_session, demo_user.id, q)
    assert status["state"] == "computed", status
    assert {r["currency"] for r in rows} == {"USD", "KRW"}

    data_team = db_session.query(Team).filter_by(user_id=demo_user.id, name="데이터팀").one()
    budget = compute_budget_status(db_session, data_team, today)
    assert budget["computable"] and Decimal(budget["ratio_pct"]) >= 100


def test_demo_cost_rows_are_deterministic():
    start, end = utc_today() - dt.timedelta(days=20), utc_today()
    a = demo_cost_rows("111122223333", start, end)
    b = DemoCostProvider().fetch({}, "111122223333", start, end).rows
    assert [(r.source_record_key, r.amount) for r in a] == [(r.source_record_key, r.amount) for r in b]
    assert all(r.period_start < utc_today() for r in a)  # 오늘은 아직 끝나지 않은 날


def test_demo_sync_keeps_resources_without_calling_csp(db_session, demo_user, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("데모 계정에서 CSP를 호출했다")

    monkeypatch.setattr(sync_router, "discover_resources", _boom)
    account = _accounts(db_session, demo_user)["prod-aws-01"]
    cred = db_session.query(Credential).filter_by(cloud_account_id=account.id, verified=True).first()
    job = ResourceSyncJob(user_id=demo_user.id, status="running", requested_at=dt.datetime.now(dt.timezone.utc))
    db_session.add(job)
    db_session.flush()
    item = ResourceSyncJobItem(sync_job_id=job.id, cloud_account_id=account.id, credential_id=cred.id, provider="aws")
    db_session.add(item)
    db_session.flush()

    sync_router._process_sync_item(db_session, item, account)

    assert item.status == "success" and item.resources_marked_stale == 0
    live = db_session.query(Resource).filter_by(cloud_account_id=account.id, is_stale=False, deleted_at=None).count()
    assert item.resources_discovered == live > 0
    assert db_session.query(Resource).filter_by(name="legacy-bastion").one().is_stale  # stale 예시는 그대로


def test_demo_resource_action_updates_db_only(client, db_session, demo_user, auth_header, monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("데모 계정에서 CSP를 호출했다")

    monkeypatch.setattr(resources_router, "perform_action", _boom)
    web = db_session.query(Resource).filter_by(name="web-prod-01").one()
    resp = client.post(
        "/api/v1/resources/action", headers={**auth_header(demo_user), "X-Action-Confirmed": "true"},
        json={"action": "stop", "resource_ids": [str(web.id)]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["results"][0]["status"] == "success"
    assert web.status == "STOPPED"


@pytest.mark.parametrize("method,path", [
    ("delete", "/api/v1/auth/me"),
    ("post", "/api/v1/credentials/aws"),
])
def test_demo_blocks_destructive_account_changes(client, demo_user, auth_header, method, path):
    headers = {**auth_header(demo_user), "X-Action-Confirmed": "true"}
    body = {"name": "x", "external_account_id": "123456789012",
            "secret_payload": {"access_key_id": "AKIAX", "secret_access_key": "y"}}
    resp = getattr(client, method)(path, headers=headers, **({"json": body} if method == "post" else {}))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "DEMO_READ_ONLY"


def test_regular_user_is_not_sandboxed(client, db_session, make_user, auth_header, demo_user):
    user = make_user("someone@example.com")
    resp = client.delete("/api/v1/auth/me", headers={**auth_header(user), "X-Action-Confirmed": "true"})
    assert resp.status_code == 204


@pytest.mark.parametrize("provider,service", [
    ("aws", "ec2"), ("aws", "s3"), ("aws", "rds"), ("aws", "cloudfront"),
    ("azure", "vm"), ("azure", "sql_database"), ("azure", "storage_account"), ("azure", "cdn"),
    ("gcp", "compute_engine"), ("gcp", "cloud_sql"), ("gcp", "cloud_storage"), ("gcp", "cloud_cdn"),
])
def test_fake_terraform_outputs_create_a_resource_row(provider, service):
    cs, ps = {"name": "demo-new-01"}, {"region": "koreacentral" if provider == "azure" else "us-east-1", "engine": "MySQL"}
    external_id, *_ = _resource_attrs(provider, service, fake_terraform_outputs(provider, service, 7, cs, ps), cs, ps)
    assert external_id
