"""PR 7 — 예산 임계 알림(app/cost/notify.py): (예산·기간·임계치)당 정확히 1회, 중복 방지 기록과
알림은 같은 트랜잭션, 수집 성공 훅에서 발화(08 §6-3 · ADR-037)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.cost.notify import evaluate_budget_thresholds, evaluate_for_account
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, Notification, Team, TeamBudget, TeamBudgetNotification

TODAY = dt.date.today()
MONTH_START = TODAY.replace(day=1)


def _setup(db, user, *, limit="300", usage="0", currency="USD"):
    account = CloudAccount(user_id=user.id, provider="aws", external_account_id="111122223333")
    db.add(account)
    db.flush()
    team = Team(user_id=user.id, name="운영팀", currency=currency)
    db.add(team)
    db.flush()
    account.team_id = team.id
    budget = TeamBudget(team_id=team.id, period_type="monthly", start_date=MONTH_START, limit_amount=Decimal(limit), currency=currency)
    db.add(budget)
    now = dt.datetime.now(dt.timezone.utc)
    db.add(CostIngestionRun(
        user_id=user.id, cloud_account_id=account.id, trigger_type="auto", status="success",
        period_start=MONTH_START, period_end=TODAY + dt.timedelta(days=1), requested_at=now, finished_at=now,
    ))
    if Decimal(usage) != 0:
        db.add(CloudAccountCost(
            cloud_account_id=account.id, provider="aws", charge_category="usage", service="AmazonEC2",
            amount=Decimal(usage), currency=currency, period_start=MONTH_START,
            period_end=MONTH_START + dt.timedelta(days=1), as_of=now, source="aws_cost_explorer",
            source_record_key=f"{account.id}:{MONTH_START}",
        ))
    db.flush()
    return account, team, budget


def _notifications(db, user):
    return db.query(Notification).filter_by(user_id=user.id, type="budget_threshold").order_by(Notification.id).all()


def test_no_notification_below_80(db_session, make_user):
    user = make_user()
    account, team, budget = _setup(db_session, user, usage="100")
    assert evaluate_budget_thresholds(db_session, team) == []
    assert _notifications(db_session, user) == []
    assert db_session.query(TeamBudgetNotification).count() == 0


def test_80_then_100_each_once(db_session, make_user):
    user = make_user()
    account, team, budget = _setup(db_session, user, usage="250")  # 83.3%
    created = evaluate_budget_thresholds(db_session, team)
    assert [n.message_params["percent"] for n in created] == [80]
    # 두 번째 평가는 아무것도 만들지 않는다
    assert evaluate_budget_thresholds(db_session, team) == []
    assert len(_notifications(db_session, user)) == 1

    db_session.add(CloudAccountCost(
        cloud_account_id=account.id, provider="aws", charge_category="usage", service="AmazonRDS",
        amount=Decimal("60"), currency="USD", period_start=MONTH_START, period_end=MONTH_START + dt.timedelta(days=1),
        as_of=dt.datetime.now(dt.timezone.utc), source="aws_cost_explorer", source_record_key=f"{account.id}:rds",
    ))
    db_session.flush()
    created = evaluate_budget_thresholds(db_session, team)  # 103.3%
    assert [n.message_params["percent"] for n in created] == [100]
    notes = _notifications(db_session, user)
    assert [n.message_params["percent"] for n in notes] == [80, 100]
    n = notes[0]
    assert n.reference_type == "team_budget" and n.reference_id == budget.id
    assert n.message_key == "notif.budget.threshold"
    assert n.message_params["team_name"] == "운영팀" and n.message_params["currency"] == "USD"
    assert n.message_params["period_start"] == MONTH_START.isoformat()
    records = db_session.query(TeamBudgetNotification).order_by(TeamBudgetNotification.threshold).all()
    assert [(r.threshold, r.notification_id) for r in records] == [(80, notes[0].id), (100, notes[1].id)]


def test_over_100_from_start_creates_both_once(db_session, make_user):
    user = make_user()
    account, team, budget = _setup(db_session, user, usage="600")  # 200%
    created = evaluate_budget_thresholds(db_session, team)
    assert [n.message_params["percent"] for n in created] == [80, 100]
    assert evaluate_budget_thresholds(db_session, team) == []
    assert len(_notifications(db_session, user)) == 2


def test_unique_violation_does_not_create_notification(db_session, make_user):
    user = make_user()
    account, team, budget = _setup(db_session, user, usage="600")
    # 다른 경로가 이미 80·100 기록을 남긴 상황(알림은 없이) — UNIQUE 위반이면 알림도 안 생긴다
    for th in (80, 100):
        db_session.add(TeamBudgetNotification(team_budget_id=budget.id, period_start=MONTH_START, threshold=th))
    db_session.flush()
    assert evaluate_budget_thresholds(db_session, team) == []
    assert _notifications(db_session, user) == []
    # 세션은 계속 쓸 수 있다(SAVEPOINT로 격리됐다)
    db_session.add(Team(user_id=user.id, name="다른팀"))
    db_session.flush()


def test_not_evaluated_when_not_computable(db_session, make_user):
    user = make_user()
    account, team, budget = _setup(db_session, user, usage="600")
    # 미수집일을 만든다 → MISSING_DAYS → 평가 안 함
    db_session.query(CostIngestionRun).delete()
    db_session.flush()
    if TODAY.day >= 3:
        assert evaluate_budget_thresholds(db_session, team) == []
        assert _notifications(db_session, user) == []


def test_evaluate_for_account_skips_unassigned_and_commits(db_session, make_user, monkeypatch):
    user = make_user()
    account, team, budget = _setup(db_session, user, usage="600")
    committed = []
    monkeypatch.setattr(db_session, "commit", lambda: committed.append(True))
    created = evaluate_for_account(db_session, account)
    assert len(created) == 2 and committed == [True]

    unassigned = CloudAccount(user_id=user.id, provider="aws", external_account_id="999")
    db_session.add(unassigned)
    db_session.flush()
    assert evaluate_for_account(db_session, unassigned) == []


def test_evaluate_for_account_swallows_errors(db_session, make_user, monkeypatch):
    user = make_user()
    account, team, budget = _setup(db_session, user, usage="600")
    import app.cost.notify as notify

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(notify, "evaluate_budget_thresholds", boom)
    rolled = []
    monkeypatch.setattr(db_session, "rollback", lambda: rolled.append(True))
    assert evaluate_for_account(db_session, account) == []
    assert rolled == [True]
