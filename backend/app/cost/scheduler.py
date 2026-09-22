"""하루 1회 자동 비용 수집(PR 4). `main.py`의 lifespan이 시작·정지를 감싼다.

테스트에서는 돌지 않는다 — `COST_SCHEDULER_ENABLED=false`(기본값)면 `start_cost_scheduler()`가
아무 일도 하지 않는다. 스케줄러가 테스트 중에 뜨면 매번 느려지고 DB 커넥션을 물고 있어 간헐
실패가 생긴다(docs/비용_개발문서/08_백엔드_구현가이드.md §4-6).
"""

from __future__ import annotations

import datetime as dt
import os

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.cost.ingest import AccountLockedError, replace_cost_rows
from app.cost.notify import evaluate_for_account
from app.cost.review import evaluate_and_notify_for_account_safely
from app.cost import COST_ADAPTERS, is_cost_supported
from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import log_background_task, log_business_event
from app.models import CloudAccount, Credential, CostIngestionRun
from app.providers.session import CredentialResolutionError, resolve_secret_payload
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json

_scheduler: BackgroundScheduler | None = None

# 자동 수집은 당월 + 최근 3일을 다시 받는다 — CSP가 어제 데이터를 늦게 확정해도 다음 날
# 실행이 재수집(범위 교체)으로 스스로 고친다(§4-6-1 "재수집이 자기 치유를 한다").
_AUTO_LOOKBACK_DAYS = 3


def _auto_period(today: dt.date) -> tuple[dt.date, dt.date]:
    month_start = today.replace(day=1)
    lookback_start = today - dt.timedelta(days=_AUTO_LOOKBACK_DAYS)
    period_start = min(month_start, lookback_start)
    return period_start, today + dt.timedelta(days=1)


def _run_single_account(db: Session, account: CloudAccount, period_start: dt.date, period_end: dt.date) -> None:
    adapter_cls = COST_ADAPTERS.get(account.provider)
    if adapter_cls is None:
        return

    credential = (
        db.query(Credential)
        .filter(Credential.cloud_account_id == account.id, Credential.verified.is_(True))
        .order_by(Credential.display_order, Credential.id)
        .first()
    )
    if credential is None:
        return

    run = CostIngestionRun(
        user_id=account.user_id,
        cloud_account_id=account.id,
        trigger_type="auto",
        status="running",
        period_start=period_start,
        period_end=period_end,
        requested_at=dt.datetime.now(dt.timezone.utc),
        started_at=dt.datetime.now(dt.timezone.utc),
    )
    db.add(run)
    db.commit()

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
        secret_payload = resolve_secret_payload(account.provider, secret_payload, credential_id=credential.id)
    except (CredentialEncryptionError, CredentialResolutionError) as exc:
        run.status = "failed"
        run.error_code = getattr(exc, "error_code", "PROVIDER_API_ERROR")
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        return

    try:
        result = adapter_cls().fetch(secret_payload, account.external_account_id, period_start, period_end)
    finally:
        del secret_payload

    run.api_calls = result.api_calls

    if result.partial:
        run.status = "partial_success"
        run.error_code = result.error_code
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        return

    try:
        replaced = replace_cost_rows(db, account, run, result.rows, source=f"{account.provider}_cost_explorer")
    except AccountLockedError:
        db.rollback()
        run = db.get(CostIngestionRun, run.id)
        run.status = "failed"
        run.error_code = "JOB_ALREADY_RUNNING"
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        return

    run.records_replaced = replaced
    run.status = "success"
    run.finished_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    # 수집이 커밋된 뒤 그 계정의 팀 예산 임계(80/100%)를 평가한다(PR 7). 실패는 로그만.
    evaluate_for_account(db, account)
    # 급증 탐지(PR 8) — 저장된 판정 대상 날 전부. 실패는 로그만.
    evaluate_and_notify_for_account_safely(db, account)


def run_daily_ingestion() -> None:
    with log_background_task("cost.daily_ingestion"):
        db = SessionLocal()
        try:
            period_start, period_end = _auto_period(dt.date.today())
            accounts = (
                db.query(CloudAccount)
                .filter(CloudAccount.provider.in_(list(COST_ADAPTERS)))
                .all()
            )
            for account in accounts:
                if not is_cost_supported(account.provider):
                    continue
                # 계정 하나가 실패해도(권한 만료 등) 나머지 계정은 계속 돈다.
                try:
                    _run_single_account(db, account, period_start, period_end)
                except Exception:  # noqa: BLE001 — 자동 수집 루프 전체가 멈추면 안 된다
                    db.rollback()
                    log_business_event("cost.daily_ingestion.account_failed", level="ERROR", cloud_account_id=account.id, exc_info=True)
            # 안전망(PR 8): 오늘 수집이 안 돌았거나 실패한 계정(자격 증명 없음·만료 등)도 저장된
            # 데이터로 새로 판정 가능해진 날을 따라잡는다 — 수집 성공에만 매달면 9/30이 10/4에
            # 판정 가능해질 때 그 계정의 수집이 멈춰 있으면 영영 빠진다. 창을 두지 않고 매번 전부 본다.
            for account in accounts:
                if is_cost_supported(account.provider):
                    evaluate_and_notify_for_account_safely(db, account)
        finally:
            db.close()


def start_cost_scheduler() -> None:
    global _scheduler
    if os.environ.get("COST_SCHEDULER_ENABLED", "false").lower() != "true":
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_daily_ingestion,
        trigger="cron",
        hour=get_settings().cost_ingest_hour_utc,
        minute=0,
        id="cost.daily_ingestion",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    log_business_event("cost.scheduler.started", hour_utc=get_settings().cost_ingest_hour_utc)


def stop_cost_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log_business_event("cost.scheduler.stopped")
