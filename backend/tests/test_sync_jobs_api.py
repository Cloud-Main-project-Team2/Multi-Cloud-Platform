"""API 명세서 v1.1 §9 리소스 동기화 API 검증.

HTTP 계층 테스트는 백그라운드 실행 함수(`_run_sync_job`)를 monkeypatch로 no-op화해서
API 계약(job/item 생성, 409/404/422, 취소)만 결정적으로 검증한다. 실제 동기화 로직
(`_process_sync_item`의 upsert/stale 처리)은 `discover_resources`를 monkeypatch해서
db_session으로 직접 호출해 검증한다 — `_run_sync_job`이 여는 `SessionLocal()`은 테스트의
트랜잭션 롤백과 무관한 별도 커넥션이라 API 테스트에서는 절대 실행시키지 않는다.
"""

from __future__ import annotations

import datetime as dt

import pytest

import app.routers.sync_jobs as sync_jobs_router
from app.models import CloudAccount, Credential, Resource, ResourceSyncJob, ResourceSyncJobItem, ServiceCatalog
from app.resource_sync import DiscoveredResource, SyncError
from app.security.credential_crypto import encrypt_credential_json


def _make_service(db_session, provider, service_code, category="compute", display_name=None):
    row = db_session.query(ServiceCatalog).filter_by(provider=provider, service_code=service_code).one_or_none()
    if row is not None:
        return row
    row = ServiceCatalog(provider=provider, service_code=service_code, category=category, display_name=display_name or service_code)
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user, provider, external_account_id, label=None):
    row = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id, account_label=label)
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account, name="cred", verified=True):
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    row = Credential(
        cloud_account_id=account.id, name=name, encrypted_payload=ciphertext, encryption_nonce=nonce,
        encryption_key_version="v1", verified=verified,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture(autouse=True)
def _noop_background_sync(monkeypatch):
    """HTTP 계층 테스트에서는 실제 백그라운드 job이 절대 돌지 않게 막는다(위 모듈 설명 참고)."""
    monkeypatch.setattr(sync_jobs_router, "_run_sync_job", lambda job_id: None)


# --- POST /sync-jobs ---------------------------------------------------------------------


def test_create_sync_job_for_all_verified_accounts(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    _make_credential(db_session, account, verified=True)
    unverified_account = _make_account(db_session, user, "gcp", "proj-1")
    _make_credential(db_session, unverified_account, verified=False)
    db_session.commit()

    resp = client.post("/api/v1/sync-jobs", json={}, headers=auth_header(user))

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["status"] == "pending"
    assert data["status_url"] == f"/api/v1/sync-jobs/{data['id']}"

    items = db_session.query(ResourceSyncJobItem).filter_by(sync_job_id=int(data["id"])).all()
    assert len(items) == 1
    assert items[0].provider == "aws"


def test_create_sync_job_with_no_verified_accounts_is_rejected(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    _make_credential(db_session, account, verified=False)
    db_session.commit()

    resp = client.post("/api/v1/sync-jobs", json={}, headers=auth_header(user))

    assert resp.status_code == 422


def test_create_sync_job_rejects_foreign_cloud_account_id(client, make_user, auth_header, db_session):
    owner = make_user(email="owner@example.com")
    intruder = make_user(email="intruder@example.com")
    account = _make_account(db_session, owner, "aws", "111122223333")
    _make_credential(db_session, account, verified=True)
    db_session.commit()

    resp = client.post(
        "/api/v1/sync-jobs", json={"cloud_account_ids": [str(account.id)]}, headers=auth_header(intruder)
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CLOUD_ACCOUNT_NOT_FOUND"


def test_create_sync_job_rejects_when_already_running(client, make_user, auth_header, db_session):
    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    _make_credential(db_session, account, verified=True)
    db_session.add(ResourceSyncJob(user_id=user.id, status="running", requested_at=dt.datetime.now(dt.timezone.utc)))
    db_session.commit()

    resp = client.post("/api/v1/sync-jobs", json={}, headers=auth_header(user))

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "JOB_ALREADY_RUNNING"


# --- GET /sync-jobs, /sync-jobs/{id} -------------------------------------------------------


def test_get_sync_job_detail_with_provider_summary(client, make_user, auth_header, db_session):

    user = make_user()
    now = dt.datetime.now(dt.timezone.utc)
    job = ResourceSyncJob(user_id=user.id, status="partial_success", requested_at=now, started_at=now, finished_at=now)
    db_session.add(job)
    db_session.flush()
    aws_account = _make_account(db_session, user, "aws", "111122223333")
    azure_account = _make_account(db_session, user, "azure", "sub-1")
    db_session.add_all([
        ResourceSyncJobItem(sync_job_id=job.id, cloud_account_id=aws_account.id, provider="aws", status="success",
                            resources_discovered=5, resources_created=5, resources_updated=0, resources_marked_stale=0),
        ResourceSyncJobItem(sync_job_id=job.id, cloud_account_id=azure_account.id, provider="azure", status="failed",
                            error_code="PROVIDER_API_ERROR"),
    ])
    db_session.commit()

    resp = client.get(f"/api/v1/sync-jobs/{job.id}", headers=auth_header(user))

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "partial_success"
    summary_by_provider = {s["provider"]: s for s in data["provider_summary"]}
    assert summary_by_provider["aws"]["status"] == "success"
    assert summary_by_provider["azure"]["status"] == "failed"
    assert summary_by_provider["azure"]["completed"] == 1


def test_get_sync_job_not_found_for_other_user(client, make_user, auth_header, db_session):

    owner = make_user(email="owner2@example.com")
    intruder = make_user(email="intruder2@example.com")
    job = ResourceSyncJob(user_id=owner.id, status="pending", requested_at=dt.datetime.now(dt.timezone.utc))
    db_session.add(job)
    db_session.commit()

    resp = client.get(f"/api/v1/sync-jobs/{job.id}", headers=auth_header(intruder))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SYNC_JOB_NOT_FOUND"


def test_list_sync_jobs_only_returns_own(client, make_user, auth_header, db_session):

    owner = make_user(email="owner3@example.com")
    other = make_user(email="other3@example.com")
    now = dt.datetime.now(dt.timezone.utc)
    db_session.add(ResourceSyncJob(user_id=owner.id, status="pending", requested_at=now))
    db_session.add(ResourceSyncJob(user_id=other.id, status="pending", requested_at=now))
    db_session.commit()

    resp = client.get("/api/v1/sync-jobs", headers=auth_header(owner))

    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 1


# --- POST /sync-jobs/{id}/cancel -----------------------------------------------------------


def test_cancel_pending_job_marks_cancelled_immediately(client, make_user, auth_header, db_session):

    user = make_user()
    job = ResourceSyncJob(user_id=user.id, status="pending", requested_at=dt.datetime.now(dt.timezone.utc))
    db_session.add(job)
    db_session.flush()
    account = _make_account(db_session, user, "aws", "111122223333")
    db_session.add(ResourceSyncJobItem(sync_job_id=job.id, cloud_account_id=account.id, provider="aws", status="pending"))
    db_session.commit()

    resp = client.post(f"/api/v1/sync-jobs/{job.id}/cancel", headers=auth_header(user))

    assert resp.status_code == 202
    assert resp.json()["data"]["status"] == "cancelled"


def test_cancel_running_job_is_best_effort_flagged(client, make_user, auth_header, db_session):

    user = make_user()
    job = ResourceSyncJob(user_id=user.id, status="running", requested_at=dt.datetime.now(dt.timezone.utc))
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/api/v1/sync-jobs/{job.id}/cancel", headers=auth_header(user))

    assert resp.status_code == 202
    assert job.id in sync_jobs_router._CANCEL_REQUESTED
    sync_jobs_router._CANCEL_REQUESTED.discard(job.id)


def test_cancel_terminal_job_is_rejected(client, make_user, auth_header, db_session):

    user = make_user()
    job = ResourceSyncJob(user_id=user.id, status="success", requested_at=dt.datetime.now(dt.timezone.utc))
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/api/v1/sync-jobs/{job.id}/cancel", headers=auth_header(user))

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "JOB_NOT_CANCELLABLE"


# --- 동기화 실행 로직(직접 호출, DB 세션 공유) -----------------------------------------------


def test_process_sync_item_creates_and_marks_stale(db_session, make_user, monkeypatch):

    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    service = _make_service(db_session, "aws", "ec2", "compute", "EC2")
    credential = _make_credential(db_session, account, verified=True)

    # 이전 동기화에서 생성된, 이번엔 더 이상 보이지 않을 리소스
    stale_candidate = Resource(
        cloud_account_id=account.id, service_catalog_id=service.id,
        provider_resource_key="aws:ec2:i-old", external_resource_id="i-old",
        original_resource_type="EC2 Instance", name="old",
        first_seen_at=dt.datetime.now(dt.timezone.utc), last_seen_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(stale_candidate)
    job = ResourceSyncJob(user_id=user.id, status="running", requested_at=dt.datetime.now(dt.timezone.utc))
    db_session.add(job)
    db_session.flush()
    item = ResourceSyncJobItem(sync_job_id=job.id, cloud_account_id=account.id, credential_id=credential.id, provider="aws", status="pending")
    db_session.add(item)
    db_session.commit()

    monkeypatch.setattr(
        sync_jobs_router, "discover_resources",
        lambda provider, secret_payload, external_account_id: [
            DiscoveredResource(service_code="ec2", external_resource_id="i-new", original_resource_type="EC2 Instance",
                                name="new", region="ap-northeast-2", status="RUNNING", tags={"env": "prod"}),
        ],
    )

    sync_jobs_router._process_sync_item(db_session, item, account)

    assert item.status == "success"
    assert item.resources_created == 1
    assert item.resources_marked_stale == 1

    db_session.refresh(stale_candidate)
    assert stale_candidate.is_stale is True

    new_resource = db_session.query(Resource).filter_by(provider_resource_key="aws:ec2:i-new").one()
    assert new_resource.status == "RUNNING"
    assert new_resource.tags == {"env": "prod"}


def test_process_sync_item_fails_without_verified_credential(db_session, make_user):

    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    job = ResourceSyncJob(user_id=user.id, status="running", requested_at=dt.datetime.now(dt.timezone.utc))
    db_session.add(job)
    db_session.flush()
    item = ResourceSyncJobItem(sync_job_id=job.id, cloud_account_id=account.id, credential_id=None, provider="aws", status="pending")
    db_session.add(item)
    db_session.commit()

    sync_jobs_router._process_sync_item(db_session, item, account)

    assert item.status == "failed"
    assert item.error_code == "CLOUD_PERMISSION_DENIED"


def test_process_sync_item_maps_sync_error(db_session, make_user, monkeypatch):

    user = make_user()
    account = _make_account(db_session, user, "aws", "111122223333")
    credential = _make_credential(db_session, account, verified=True)
    job = ResourceSyncJob(user_id=user.id, status="running", requested_at=dt.datetime.now(dt.timezone.utc))
    db_session.add(job)
    db_session.flush()
    item = ResourceSyncJobItem(sync_job_id=job.id, cloud_account_id=account.id, credential_id=credential.id, provider="aws", status="pending")
    db_session.add(item)
    db_session.commit()

    def _raise(provider, secret_payload, external_account_id):
        raise SyncError("PROVIDER_API_ERROR")

    monkeypatch.setattr(sync_jobs_router, "discover_resources", _raise)

    sync_jobs_router._process_sync_item(db_session, item, account)

    assert item.status == "failed"
    assert item.error_code == "PROVIDER_API_ERROR"


def test_aggregate_status_partial_success():
    assert sync_jobs_router._aggregate_status(["success", "failed"]) == "partial_success"
    assert sync_jobs_router._aggregate_status(["success", "success"]) == "success"
    assert sync_jobs_router._aggregate_status(["failed", "failed"]) == "failed"
    assert sync_jobs_router._aggregate_status(["pending", "pending"]) == "pending"
    assert sync_jobs_router._aggregate_status(["running", "pending"]) == "running"
    assert sync_jobs_router._aggregate_status(["cancelled", "cancelled"]) == "cancelled"
