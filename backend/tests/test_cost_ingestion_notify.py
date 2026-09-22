"""비용 새로고침(수동 `POST /cost-ingestion-runs`) 완료 알림 검증(4차 항목 2).

`_run_cost_ingestion_run_inner` 전체(자격증명 복호화·CSP 어댑터 호출)는 기존에도 테스트가
없다 — 여기서는 새로 추가한 `_create_cost_ingestion_notification` 헬퍼만 단위로 검증한다.
자동 수집(app/cost/scheduler.py)은 별도 구현이라 이 알림 경로와 무관하다.
"""

from __future__ import annotations

import datetime as dt

import app.routers.costs as costs_router
from app.models import CloudAccount, CostIngestionRun, Notification


def _make_account(db_session, user, provider="aws", external_account_id="111122223333", label="운영 계정"):
    account = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id, account_label=label)
    db_session.add(account)
    db_session.flush()
    return account


def _make_run(db_session, user, account, status, **extra):
    run = CostIngestionRun(
        user_id=user.id, cloud_account_id=account.id, trigger_type="manual", status=status,
        period_start=dt.date(2026, 9, 1), period_end=dt.date(2026, 9, 20),
        requested_at=dt.datetime.now(dt.timezone.utc), **extra,
    )
    db_session.add(run)
    db_session.flush()
    return run


def test_success_run_creates_succeeded_notification(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    run = _make_run(db_session, user, account, "success", records_replaced=42)

    costs_router._create_cost_ingestion_notification(db_session, run, account)
    db_session.commit()

    notif = db_session.query(Notification).filter_by(user_id=user.id, type="cost_ingestion_succeeded").one()
    assert notif.reference_type == "cost_ingestion_run"
    assert notif.reference_id == run.id
    assert notif.message_params["records_replaced"] == 42
    assert notif.message_params["account_name"] == "운영 계정"


def test_failed_run_creates_failed_notification_with_reason(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    run = _make_run(db_session, user, account, "failed", error_code="CLOUD_PERMISSION_DENIED", error_message="검증된 자격 증명이 없습니다.")

    costs_router._create_cost_ingestion_notification(db_session, run, account)
    db_session.commit()

    notif = db_session.query(Notification).filter_by(user_id=user.id, type="cost_ingestion_failed").one()
    assert notif.message_params["reason"] == "검증된 자격 증명이 없습니다."


def test_partial_success_is_treated_as_failure_notification(db_session, make_user):
    """부분 응답으로 갈아치우지 않은 상태를 "성공" 배지로 알리면 원인을 놓친다(§4-4)."""
    user = make_user()
    account = _make_account(db_session, user)
    run = _make_run(db_session, user, account, "partial_success", error_code="PROVIDER_API_ERROR")

    costs_router._create_cost_ingestion_notification(db_session, run, account)
    db_session.commit()

    notif = db_session.query(Notification).filter_by(user_id=user.id, type="cost_ingestion_failed").one()
    assert notif.message_params["reason"] == "PROVIDER_API_ERROR"
