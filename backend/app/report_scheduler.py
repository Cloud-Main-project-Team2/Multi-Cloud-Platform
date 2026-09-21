"""보고서 "정기 발송" 메일 스케줄러(2026-09-19) — `app/cost/scheduler.py`와 동일 패턴
(apscheduler `BackgroundScheduler`, 단일 프로세스 전제, `REPORT_SCHEDULER_ENABLED` 기본값
false라 테스트 중에는 안 돈다 — 스케줄러가 테스트마다 뜨면 느려지고 DB 커넥션을 물고 있어
간헐 실패가 생긴다, cost 스케줄러와 동일 이유).

메일 본문(텍스트+HTML) 생성은 `app/report_email.py`가 맡는다 — 이 파일은 "언제 보낼지"만
판단한다."""

from __future__ import annotations

import datetime as dt
import os

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import log_background_task, log_business_event
from app.mailer import send_email
from app.models import ReportDeliverySetting, User
from app.report_email import build_report_email

_scheduler: BackgroundScheduler | None = None

# "그 주기마다"의 최소 구현 — 캘린더 정렬(매주 월요일 등)까진 요구되지 않았다(§4.3). 마지막
# 발송 이후 이 일수가 지났으면 다시 보낸다.
_PERIOD_DAYS = {"DAILY": 1, "WEEKLY": 7, "MONTHLY": 30, "HALF_YEARLY": 182}
_PERIOD_LABEL = {"DAILY": "일간", "WEEKLY": "주간", "MONTHLY": "월간", "HALF_YEARLY": "반기"}


def is_due(period_type: str, last_sent_at: dt.datetime | None, now: dt.datetime) -> bool:
    """한 번도 안 보냈으면 바로 대상이다. 그 외엔 주기(일수)가 지났는지로만 판단한다."""
    if last_sent_at is None:
        return True
    days = _PERIOD_DAYS.get(period_type, 7)
    return now - last_sent_at >= dt.timedelta(days=days)


def _send_one(db: Session, row: ReportDeliverySetting, now: dt.datetime) -> bool:
    """설정 1건을 처리한다. 실제로 발송했으면 True — `sync_jobs.py`의 `_process_sync_item`과
    동일하게 `db`를 인자로 받아서, 테스트가 `SessionLocal()`을 거치지 않고 `db_session`으로
    직접 호출해 검증할 수 있게 한다."""
    if not is_due(row.period_type, row.last_sent_at, now):
        return False
    user = db.get(User, row.user_id)
    if user is None or not row.email:
        return False

    text, html_body = build_report_email(db, user)
    subject = f"[MultiCloud Ops] {_PERIOD_LABEL.get(row.period_type, row.period_type)} 보고서"
    send_email(row.email, subject, text, html_body=html_body)
    row.last_sent_at = now
    db.commit()
    log_business_event("report.email.sent", user_id=user.id, period_type=row.period_type)
    return True


def send_due_reports() -> None:
    with log_background_task("report.send_due"):
        db = SessionLocal()
        try:
            now = dt.datetime.now(dt.timezone.utc)
            rows = db.query(ReportDeliverySetting).filter(ReportDeliverySetting.delivery_method == "EMAIL").all()
            for row in rows:
                # 계정 하나가 실패해도(메일 서버 오류 등) 나머지 계정은 계속 돈다.
                try:
                    _send_one(db, row, now)
                except Exception:  # noqa: BLE001 — 발송 루프 전체가 멈추면 안 된다
                    db.rollback()
                    log_business_event(
                        "report.email.failed", level="ERROR", user_id=row.user_id, exc_info=True
                    )
        finally:
            db.close()


def start_report_scheduler() -> None:
    global _scheduler
    if os.environ.get("REPORT_SCHEDULER_ENABLED", "false").lower() != "true":
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        send_due_reports,
        trigger="cron",
        hour=get_settings().report_send_hour_utc,
        minute=0,
        id="report.send_due",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    log_business_event("report.scheduler.started", hour_utc=get_settings().report_send_hour_utc)


def stop_report_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log_business_event("report.scheduler.stopped")
