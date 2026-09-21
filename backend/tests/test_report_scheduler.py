"""app/report_scheduler.py 단위 테스트(2026-09-19) — 보고서 정기 메일 발송.

`SessionLocal()`을 여는 `send_due_reports()`는 실행하지 않는다(테스트 트랜잭션과 무관한 별도
커넥션이라 `test_sync_jobs_api.py`와 동일 이유) — 대신 `db`를 인자로 받는 `_send_one()`을
`db_session`으로 직접 호출해 검증한다. 실제 메일 발송(`send_email`)은 monkeypatch로 막는다.
"""

from __future__ import annotations

import datetime as dt

import app.report_scheduler as report_scheduler
from app.models import ReportDeliverySetting


def _make_setting(db_session, user, **kwargs):
    row = ReportDeliverySetting(
        user_id=user.id, delivery_method="EMAIL", email="me@example.com", period_type="WEEKLY", **kwargs
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_is_due_when_never_sent():
    assert report_scheduler.is_due("WEEKLY", None, dt.datetime.now(dt.timezone.utc)) is True


def test_is_due_false_within_period():
    now = dt.datetime.now(dt.timezone.utc)
    assert report_scheduler.is_due("WEEKLY", now - dt.timedelta(days=1), now) is False


def test_is_due_true_after_period_elapsed():
    now = dt.datetime.now(dt.timezone.utc)
    assert report_scheduler.is_due("WEEKLY", now - dt.timedelta(days=8), now) is True


def test_is_due_daily_vs_half_yearly_thresholds():
    now = dt.datetime.now(dt.timezone.utc)
    two_days_ago = now - dt.timedelta(days=2)
    assert report_scheduler.is_due("DAILY", two_days_ago, now) is True
    assert report_scheduler.is_due("HALF_YEARLY", two_days_ago, now) is False


def test_send_one_skips_when_not_due(db_session, make_user, monkeypatch):
    user = make_user()
    now = dt.datetime.now(dt.timezone.utc)
    row = _make_setting(db_session, user, last_sent_at=now - dt.timedelta(days=1))

    called = []
    monkeypatch.setattr(report_scheduler, "send_email", lambda *a, **k: called.append(1))

    sent = report_scheduler._send_one(db_session, row, now)
    assert sent is False
    assert called == []


def test_send_one_sends_and_updates_last_sent_at(db_session, make_user, monkeypatch):
    user = make_user()
    row = _make_setting(db_session, user, last_sent_at=None)

    captured = {}
    monkeypatch.setattr(
        report_scheduler, "send_email",
        lambda to, subject, body, html_body=None: captured.update(
            to=to, subject=subject, body=body, html_body=html_body
        ),
    )

    now = dt.datetime.now(dt.timezone.utc)
    sent = report_scheduler._send_one(db_session, row, now)

    assert sent is True
    assert captured["to"] == "me@example.com"
    assert "주간 보고서" in captured["subject"]
    assert captured["html_body"]  # HTML 버전도 함께 전달돼야 한다(app/report_email.py)
    assert row.last_sent_at == now


def test_send_one_skips_when_no_email(db_session, make_user, monkeypatch):
    # DB에는 delivery_method='EMAIL' + email=NULL을 막는 CHECK 제약이 있어(API도 이미
    # 막는다), 이 조합은 영속화하지 않고 인메모리 객체로만 만들어 방어 코드 자체를 검증한다.
    user = make_user()
    row = ReportDeliverySetting(user_id=user.id, delivery_method="EMAIL", email=None, period_type="WEEKLY")

    called = []
    monkeypatch.setattr(report_scheduler, "send_email", lambda *a, **k: called.append(1))

    sent = report_scheduler._send_one(db_session, row, dt.datetime.now(dt.timezone.utc))
    assert sent is False
    assert called == []
