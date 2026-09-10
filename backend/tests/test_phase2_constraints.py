"""Behavioral checks for the phase-2 additions: provisioning_jobs,
cloud_resource_costs. Uses the ORM models directly against the schema built by
conftest.py's create_all()-based `engine`/`db_session` fixtures.

이전 API 명세(provider별 개별 엔드포인트) 유지 결정으로 provisioning_requests /
resource_types 및 관련 컬럼은 제거됐다(0caab346f140).
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    CloudAccount,
    CloudResourceCost,
    Credential,
    ProvisioningJob,
    Resource,
    ServiceCatalog,
    User,
)


def _make_user_account_credential(session):
    user = User(
        email="phase2@example.com",
        normalized_email="phase2@example.com",
        name="Phase2 User",
        affiliation_type="individual",
    )
    session.add(user)
    session.flush()

    account = CloudAccount(user_id=user.id, provider="aws", external_account_id="222222222222")
    session.add(account)
    session.flush()

    credential = Credential(
        cloud_account_id=account.id,
        name="phase2-cred",
        encrypted_payload=b"\x00",
        encryption_nonce=b"\x00",
        encryption_key_version="v1",
    )
    session.add(credential)
    session.flush()
    return user, account, credential


def _make_ec2_catalog(session):
    catalog = ServiceCatalog(
        provider="aws", service_code="ec2", category="compute", display_name="EC2", provisionable=True
    )
    session.add(catalog)
    session.flush()
    return catalog


def test_job_idempotency_key_unique_per_user(db_session):
    user, _account, credential = _make_user_account_credential(db_session)
    catalog = _make_ec2_catalog(db_session)

    job1 = ProvisioningJob(
        user_id=user.id,
        credential_id=credential.id,
        service_catalog_id=catalog.id,
        workspace_name="ws-a",
        idempotency_key="same-idem",
        spec_json={},
    )
    db_session.add(job1)
    db_session.flush()

    job2 = ProvisioningJob(
        user_id=user.id,
        credential_id=credential.id,
        service_catalog_id=catalog.id,
        workspace_name="ws-b",
        idempotency_key="same-idem",
        spec_json={},
    )
    db_session.add(job2)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_progress_percent_range_check(db_session):
    user, _account, credential = _make_user_account_credential(db_session)
    catalog = _make_ec2_catalog(db_session)

    over_limit = ProvisioningJob(
        user_id=user.id,
        credential_id=credential.id,
        service_catalog_id=catalog.id,
        workspace_name="ws-over",
        idempotency_key="idem-over",
        spec_json={},
        progress_percent=150,
    )
    db_session.add(over_limit)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_progress_percent_100_is_allowed(db_session):
    user, _account, credential = _make_user_account_credential(db_session)
    catalog = _make_ec2_catalog(db_session)

    complete = ProvisioningJob(
        user_id=user.id,
        credential_id=credential.id,
        service_catalog_id=catalog.id,
        workspace_name="ws-complete",
        idempotency_key="idem-complete",
        spec_json={},
        progress_percent=100,
    )
    db_session.add(complete)
    db_session.flush()
    assert complete.id is not None


def _make_cost_kwargs(resource_id, source_record_key, **overrides):
    kwargs = dict(
        resource_id=resource_id,
        provider="aws",
        cost_kind="actual",
        amount=10,
        currency="USD",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 9),
        as_of=datetime.now(timezone.utc),
        source="aws_cost_explorer",
        source_record_key=source_record_key,
    )
    kwargs.update(overrides)
    return kwargs


def _make_resource(session, account, catalog):
    resource = Resource(
        cloud_account_id=account.id,
        service_catalog_id=catalog.id,
        provider_resource_key=f"arn:aws:ec2:x:1:instance/i-{id(object())}",
        external_resource_id="i-cost-test",
        original_resource_type="AWS::EC2::Instance",
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(resource)
    session.flush()
    return resource


def test_cloud_resource_cost_duplicate_source_record_key_rejected(db_session):
    _user, account, _credential = _make_user_account_credential(db_session)
    catalog = _make_ec2_catalog(db_session)
    resource = _make_resource(db_session, account, catalog)

    db_session.add(CloudResourceCost(**_make_cost_kwargs(resource.id, "rec-dup")))
    db_session.flush()

    db_session.add(CloudResourceCost(**_make_cost_kwargs(resource.id, "rec-dup")))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_cloud_resource_cost_negative_amount_rejected(db_session):
    _user, account, _credential = _make_user_account_credential(db_session)
    catalog = _make_ec2_catalog(db_session)
    resource = _make_resource(db_session, account, catalog)

    db_session.add(CloudResourceCost(**_make_cost_kwargs(resource.id, "rec-neg", amount=-1)))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_cloud_resource_cost_period_end_before_start_rejected(db_session):
    _user, account, _credential = _make_user_account_credential(db_session)
    catalog = _make_ec2_catalog(db_session)
    resource = _make_resource(db_session, account, catalog)

    db_session.add(
        CloudResourceCost(
            **_make_cost_kwargs(
                resource.id, "rec-bad-period", period_start=date(2026, 9, 9), period_end=date(2026, 9, 1)
            )
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
