"""PR 8 — 급증 탐지 규칙(app/cost/anomaly.py)과 자동 탐지→큐·알림 경로(app/cost/review.py).

실제 CSP를 호출하지 않는다. 날짜는 `today`를 함수 인자로 고정해 결정적으로 만든다(UTC 경계).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.cost.anomaly import (
    HELD_BASELINE_INCOMPLETE,
    MIN_HISTORY_DAYS,
    detect_anomalies,
    eligible_end,
    evaluate_account,
    evaluate_account_all_stored,
    parse_source_key,
)
from app.cost.coverage import covered_days
from app.cost.review import evaluate_and_notify_account
from app.models import CloudAccount, CloudAccountCost, CostIngestionRun, CostReviewItem, Notification

D = dt.timedelta


def _account(db, user, provider="aws", ext="111122223333"):
    a = CloudAccount(user_id=user.id, provider=provider, external_account_id=ext, account_label="prod-aws")
    db.add(a)
    db.flush()
    return a


def _run(db, account, start, end, status="success"):
    now = dt.datetime.now(dt.timezone.utc)
    r = CostIngestionRun(
        user_id=account.user_id, cloud_account_id=account.id, trigger_type="auto", status=status,
        period_start=start, period_end=end, requested_at=now, finished_at=now,
    )
    db.add(r)
    db.flush()
    return r


def _cost(db, account, day, amount, service="AmazonEC2", currency="USD", charge_category="usage", source="aws_cost_explorer"):
    row = CloudAccountCost(
        cloud_account_id=account.id, provider=account.provider, charge_category=charge_category, service=service,
        amount=Decimal(amount), currency=currency, period_start=day, period_end=day + D(days=1),
        as_of=dt.datetime.now(dt.timezone.utc), source=source,
        source_record_key=f"{account.id}:{service}:{day}:{charge_category}:{currency}:{amount}",
    )
    db.add(row)
    db.flush()
    return row


def _flat(db, account, start, days, amount="10", **kw):
    """start부터 days일 동안 매일 같은 금액 + 그 범위를 덮는 성공 run."""
    for i in range(days):
        _cost(db, account, start + D(days=i), amount, **kw)
    _run(db, account, start, start + D(days=days))


TODAY = dt.date(2026, 10, 10)  # 고정 — eligible_end = 10/7, 마지막 판정일 10/6


def test_eligible_end_excludes_recent_three_days():
    assert eligible_end(TODAY) == dt.date(2026, 10, 7)  # 판정일 ≤ 10/6 (오늘·어제·그제·그끄제 제외)


def test_insufficient_history_when_only_11_days(db_session, make_user):
    a = _account(db_session, make_user())
    start = dt.date(2026, 9, 26)
    _flat(db_session, a, start, 11)  # 9/26~10/6 = 11일
    ev = evaluate_account(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert ev.items == []
    assert ev.insufficient_history == {"days_available": 11, "days_required": MIN_HISTORY_DAYS}


def test_history_counts_only_days_through_judgment_day(db_session, make_user):
    """9/26~10/6에 11일 + 10/7~10/20에 14일이 더 있어도, 10/6 판정의 이력은 11일이다."""
    a = _account(db_session, make_user())
    _flat(db_session, a, dt.date(2026, 9, 26), 11)
    _flat(db_session, a, dt.date(2026, 10, 7), 14)
    ev = evaluate_account(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert ev.insufficient_history == {"days_available": 11, "days_required": MIN_HISTORY_DAYS}
    # 10/8은 이력 13일이라 판정 대상 — (같은 금액이라 급증은 아님, held도 아님)
    ev2 = evaluate_account(db_session, a, dt.date(2026, 10, 8), dt.date(2026, 10, 9))
    assert ev2.insufficient_history is None and ev2.held == [] and ev2.items == []


def test_detects_spike_with_and_condition(db_session, make_user):
    a = _account(db_session, make_user())
    _flat(db_session, a, dt.date(2026, 9, 20), 16, amount="10")  # 9/20~10/5 = 16일, 기준선 10
    _cost(db_session, a, dt.date(2026, 10, 6), "18.4")  # +8.4 (≥5) · +84% (≥50) → 급증
    _run(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    ev = evaluate_account(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert [it.date for it in ev.items] == [dt.date(2026, 10, 6)]
    it = ev.items[0]
    assert it.amount == Decimal("18.400000") and it.baseline_amount == Decimal("10.000000")
    assert it.delta == Decimal("8.400000") and it.delta_pct == Decimal("84.0")
    assert it.source_key == f"{a.id}:AmazonEC2:2026-10-06"


def test_delta_only_or_pct_only_is_not_a_spike(db_session, make_user):
    a = _account(db_session, make_user())
    _flat(db_session, a, dt.date(2026, 9, 20), 16, amount="100")  # 기준선 100
    _cost(db_session, a, dt.date(2026, 10, 6), "120")  # +20 (≥5) but +20% (<50) → 아니다
    _run(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert evaluate_account(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7)).items == []

    b = _account(db_session, make_user(email="b@example.com"), ext="2")
    _flat(db_session, b, dt.date(2026, 9, 20), 16, amount="1")  # 기준선 1
    _cost(db_session, b, dt.date(2026, 10, 6), "4")  # +3 (<5) but +300% → 아니다
    _run(db_session, b, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert evaluate_account(db_session, b, dt.date(2026, 10, 6), dt.date(2026, 10, 7)).items == []


def test_baseline_zero_exception_detects_with_null_pct(db_session, make_user):
    """기준선 0 예외 — 비율식을 적용하지 않고 최소 차액만으로 탐지, delta_pct는 None."""
    a = _account(db_session, make_user())
    _flat(db_session, a, dt.date(2026, 9, 20), 16, amount="10", service="AmazonEC2")
    _cost(db_session, a, dt.date(2026, 10, 6), "7", service="AmazonRDS")  # RDS는 이전 7일 0(수집 확인됨)
    _run(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    ev = evaluate_account(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    rds = [it for it in ev.items if it.service == "AmazonRDS"]
    assert len(rds) == 1 and rds[0].delta_pct is None and rds[0].delta == Decimal("7.000000")
    # 기준선 0 + 최소 차액 미만이면 아니다
    b = _account(db_session, make_user(email="b@example.com"), ext="2")
    _flat(db_session, b, dt.date(2026, 9, 20), 16, amount="10")
    _cost(db_session, b, dt.date(2026, 10, 6), "4.99", service="AmazonRDS")
    _run(db_session, b, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert [it.service for it in evaluate_account(db_session, b, dt.date(2026, 10, 6), dt.date(2026, 10, 7)).items] == []


def test_missing_day_in_baseline_holds_instead_of_zero(db_session, make_user):
    """기준선 7일 중 하루가 미수집이면 판정 보류(held). 0으로 넣었다면 평균이 낮아져 급증으로 잡혔을 값이다."""
    a = _account(db_session, make_user())
    # 9/20~10/5 행은 있지만 run은 10/2 하루를 빼고 덮는다 → 10/2에 행도 없고 run도 없다 = 미수집
    for i in range(16):
        day = dt.date(2026, 9, 20) + D(days=i)
        if day != dt.date(2026, 10, 2):
            _cost(db_session, a, day, "10")
    _run(db_session, a, dt.date(2026, 9, 20), dt.date(2026, 10, 2))
    _run(db_session, a, dt.date(2026, 10, 3), dt.date(2026, 10, 7))
    _cost(db_session, a, dt.date(2026, 10, 6), "14")  # 0 취급이면 기준선 8.57 → +5.4/+63% 급증. 보류가 맞다
    ev = evaluate_account(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert ev.items == []
    assert ev.held == [{"date": dt.date(2026, 10, 6), "reason": HELD_BASELINE_INCOMPLETE}]


def test_zero_cost_collected_day_counts_as_covered_and_not_duplicated(db_session, make_user):
    a = _account(db_session, make_user())
    # 서비스 행 2개인 날 + 겹치는 run 2개 → 날짜는 한 번만
    _cost(db_session, a, dt.date(2026, 10, 1), "1", service="AmazonEC2")
    _cost(db_session, a, dt.date(2026, 10, 1), "2", service="AmazonRDS")
    _run(db_session, a, dt.date(2026, 9, 30), dt.date(2026, 10, 3))
    _run(db_session, a, dt.date(2026, 10, 1), dt.date(2026, 10, 5))
    days = covered_days(db_session, a.id, dt.date(2026, 9, 29), dt.date(2026, 10, 6))
    assert days == {dt.date(2026, 9, 30), dt.date(2026, 10, 1), dt.date(2026, 10, 2), dt.date(2026, 10, 3), dt.date(2026, 10, 4)}


def test_credit_rows_do_not_affect_judgment(db_session, make_user):
    a = _account(db_session, make_user())
    _flat(db_session, a, dt.date(2026, 9, 20), 16, amount="10")
    _cost(db_session, a, dt.date(2026, 10, 3), "-50", charge_category="credit")  # 순액으로 보면 10/3이 푹 꺼진다
    _cost(db_session, a, dt.date(2026, 10, 6), "12", charge_category="usage")
    _run(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    ev = evaluate_account(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert ev.items == []  # 기준선은 usage 10 그대로 → +2는 급증 아님


def test_unsupported_currency_is_reported_not_dropped(db_session, make_user):
    a = _account(db_session, make_user())
    _flat(db_session, a, dt.date(2026, 9, 20), 17, amount="1000", currency="KRW")
    ev = evaluate_account(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    assert ev.unsupported_currency is True and ev.items == []
    d = detect_anomalies(db_session, a.user_id, [a], dt.date(2026, 10, 1), dt.date(2026, 10, 8), today=TODAY)
    assert d["unsupported_currency"] == [{"cloud_account_id": a.id, "provider": "aws", "currency": "KRW"}]
    assert d["rule"]["currency"] == "USD"


def test_parse_source_key_with_colon_in_service():
    assert parse_source_key("31:Amazon:Weird:Svc:2026-09-13") == (31, "Amazon:Weird:Svc", dt.date(2026, 9, 13))


# --- 자동 탐지 → 큐·알림 -----------------------------------------------------------------------


def _notes(db, user):
    return db.query(Notification).filter_by(user_id=user.id, type="cost_anomaly").order_by(Notification.id).all()


def test_month_boundary_spike_is_caught_after_it_becomes_eligible(db_session, make_user):
    """9/30 급증은 10/4 이후에야 판정 가능. 그때 자동 수집 범위(당월+3일)는 10/1부터라 '이번 run 범위'만
    보면 놓친다 — 저장된 판정 대상 날 전부를 보므로 잡힌다."""
    user = make_user()
    a = _account(db_session, user)
    _flat(db_session, a, dt.date(2026, 9, 10), 20, amount="10")  # 9/10~9/29
    _cost(db_session, a, dt.date(2026, 9, 30), "30")
    _run(db_session, a, dt.date(2026, 9, 30), dt.date(2026, 10, 4))  # 10/1~10/3은 $0 수집 확인
    assert evaluate_and_notify_account(db_session, a, today=dt.date(2026, 10, 3)) == []  # 9/30은 아직 최근 3일 안
    created = evaluate_and_notify_account(db_session, a, today=dt.date(2026, 10, 4))
    assert [c.source_key for c in created] == [f"{a.id}:AmazonEC2:2026-09-30"]
    assert len(_notes(db_session, user)) == 1
    n = _notes(db_session, user)[0]
    assert n.reference_type == "cost_review_item" and n.reference_id == created[0].id
    assert n.message_params["delta"] == "20.000000" and n.message_params["currency"] == "USD"
    # 재평가·재수집(같은 날 금액 변경)에도 재알림 없음
    _cost(db_session, a, dt.date(2026, 9, 30), "1")  # 금액이 더 늘어도
    assert evaluate_and_notify_account(db_session, a, today=dt.date(2026, 10, 5)) == []
    assert len(_notes(db_session, user)) == 1


def test_long_outage_recovery_catches_all_missed_days_once(db_session, make_user):
    """14일 넘게 평가가 멈췄다가 재개되면 그 사이 판정 가능해진 급증이 전부, 각 한 번씩 잡힌다."""
    user = make_user()
    a = _account(db_session, user)
    _flat(db_session, a, dt.date(2026, 9, 1), 40, amount="10")  # 9/1~10/10
    for day in (dt.date(2026, 9, 20), dt.date(2026, 10, 1), dt.date(2026, 10, 5)):
        _cost(db_session, a, day, "25")  # 각 날 35 → 기준선(직전 7일)은 급증일이 섞여도 ≥ 50% 넘는다
    created = evaluate_and_notify_account(db_session, a, today=dt.date(2026, 11, 1))
    assert sorted(c.source_key.rsplit(":", 1)[1] for c in created) == ["2026-09-20", "2026-10-01", "2026-10-05"]
    assert len(_notes(db_session, user)) == 3
    assert evaluate_and_notify_account(db_session, a, today=dt.date(2026, 11, 2)) == []
    assert db_session.query(CostReviewItem).filter_by(user_id=user.id).count() == 3


def test_failed_then_retried_ingestion_and_past_recollection(db_session, make_user):
    user = make_user()
    a = _account(db_session, user)
    _flat(db_session, a, dt.date(2026, 9, 1), 30, amount="10")  # 9/1~9/30
    _run(db_session, a, dt.date(2026, 10, 1), dt.date(2026, 10, 3), status="failed")  # 실패 run은 수집 확인이 아니다
    _cost(db_session, a, dt.date(2026, 10, 2), "40")
    # 10/1이 미수집이라 10/2 판정은 기준선 미완성 → 보류
    ev = evaluate_account_all_stored(db_session, a, today=dt.date(2026, 10, 8))
    assert ev.items == [] and any(h["date"] == dt.date(2026, 10, 2) for h in ev.held)
    # 재시도 성공(과거 재수집) → 10/1 $0 확인 → 10/2 급증 판정
    _run(db_session, a, dt.date(2026, 10, 1), dt.date(2026, 10, 3), status="success")
    created = evaluate_and_notify_account(db_session, a, today=dt.date(2026, 10, 8))
    assert [c.source_key for c in created] == [f"{a.id}:AmazonEC2:2026-10-02"]


def test_detect_anomalies_api_shape_related_changes_and_sample_flag(db_session, make_user):
    from app.models import Credential, ProvisioningJob, ServiceCatalog
    from app.security.credential_crypto import encrypt_credential_json

    user = make_user()
    a = _account(db_session, user)
    _flat(db_session, a, dt.date(2026, 9, 20), 16, amount="10", source="seed-aws-actual")
    _cost(db_session, a, dt.date(2026, 10, 6), "30", source="seed-aws-actual")
    _run(db_session, a, dt.date(2026, 10, 6), dt.date(2026, 10, 7))
    svc = db_session.query(ServiceCatalog).filter_by(provider="aws", service_code="ec2").first()
    if svc is None:
        svc = ServiceCatalog(provider="aws", service_code="ec2", category="compute", display_name="EC2", provisionable=True)
        db_session.add(svc)
        db_session.flush()
    ciphertext, nonce = encrypt_credential_json({"x": "y"})
    cred = Credential(cloud_account_id=a.id, name="c", encrypted_payload=ciphertext, encryption_nonce=nonce, encryption_key_version="v1", verified=True)
    db_session.add(cred)
    db_session.flush()
    finished = dt.datetime(2026, 10, 6, 4, 20, tzinfo=dt.timezone.utc)
    job = ProvisioningJob(
        user_id=user.id, credential_id=cred.id, service_catalog_id=svc.id, status="success", idempotency_key="k1",
        workspace_name="ws-1", spec_json={}, finished_at=finished,
    )
    db_session.add(job)
    db_session.flush()

    d = detect_anomalies(db_session, user.id, [a], dt.date(2026, 10, 1), dt.date(2026, 10, 8), today=TODAY)
    assert d["total"] == 1
    it = d["items"][0]
    assert it["label"] == "원인 확인 필요" and it["is_sample_data"] is True and it["review"] is None
    assert it["related_changes"] == [{"type": "provisioning_job", "id": job.id, "service_code": "ec2", "finished_at": finished}]
    assert d["rule"]["min_delta_amount"] == "5" and d["rule"]["charge_category"] == ["usage"]
