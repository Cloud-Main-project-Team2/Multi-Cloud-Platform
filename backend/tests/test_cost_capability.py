"""PR 5 — `app/cost/capability.py` 상태 9종 판정 검증(docs/01_API_Specification_v1.2.md §11-3).

CSP를 호출하지 않는다 — DB에 심어 둔 credential/cost_ingestion_runs 행만으로 판정한다.
"""

from __future__ import annotations

import datetime as dt

from app.cost.capability import account_capability
from app.models import CloudAccount, CostIngestionRun, Credential
from app.security.credential_crypto import encrypt_credential_json

NOW = dt.datetime.now(dt.timezone.utc)


def _make_account(db_session, user, provider="aws", external_account_id="111122223333") -> CloudAccount:
    account = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_account_id)
    db_session.add(account)
    db_session.flush()
    return account


def _make_credential(db_session, account, verified=True) -> Credential:
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    credential = Credential(
        cloud_account_id=account.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
        encryption_key_version="v1", verified=verified,
        permission_scope={"cost_read": True} if verified else {},
    )
    db_session.add(credential)
    db_session.flush()
    return credential


def _make_run(db_session, account, *, status, error_code=None, records_replaced=0, finished_delta_hours=1):
    now = dt.datetime.now(dt.timezone.utc)
    run = CostIngestionRun(
        user_id=account.user_id, cloud_account_id=account.id, trigger_type="manual", status=status,
        period_start=dt.date(2026, 9, 1), period_end=dt.date(2026, 9, 18),
        requested_at=now - dt.timedelta(hours=finished_delta_hours),
        started_at=now - dt.timedelta(hours=finished_delta_hours),
        finished_at=now - dt.timedelta(hours=finished_delta_hours) if status not in ("pending", "running") else None,
        records_replaced=records_replaced, error_code=error_code,
    )
    db_session.add(run)
    db_session.flush()
    return run


# --- provider 미구현 -----------------------------------------------------------------------


def test_provider_without_adapter_is_unsupported_not_permission_denied(db_session, make_user):
    """2026-09-23: Azure 수집기가 생겨서 "어댑터 없음" 예시를 GCP로 바꿨다. 지키는 성질은 그대로 —
    수집기가 없는 provider는 UNSUPPORTED이지 PERMISSION_DENIED가 아니다(하드코딩된 cost_read=false를
    권한 거절로 읽으면 사용자에게 없는 죄를 씌운다)."""
    user = make_user()
    account = _make_account(db_session, user, provider="gcp", external_account_id="proj-1")
    _make_credential(db_session, account)  # 검증된 credential이 있어도 UNSUPPORTED가 우선이다

    cap = account_capability(db_session, account)

    assert cap["status"] == "UNSUPPORTED"
    assert cap["status"] != "PERMISSION_DENIED"
    assert cap["cost_read"] is None  # UNSUPPORTED에서만 null
    assert cap["capability_source"] == "not_implemented"


def test_gcp_is_unsupported_not_permission_denied(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user, provider="gcp", external_account_id="proj-1")

    cap = account_capability(db_session, account)

    assert cap["status"] == "UNSUPPORTED"
    assert cap["cost_read"] is None


# --- 계정 연결 상태 --------------------------------------------------------------------------


def test_no_verified_credential_is_not_connected(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)

    cap = account_capability(db_session, account)

    assert cap["status"] == "NOT_CONNECTED"
    assert cap["cost_read"] is False  # null이 아니다 — UNSUPPORTED 전용


def test_verified_but_never_collected_is_pending(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)

    cap = account_capability(db_session, account)

    assert cap["status"] == "PENDING"
    assert cap["as_of"] is None


# --- 수집 성공/실패 매핑 (QA-01 구분) ---------------------------------------------------------


def test_success_with_rows_is_connected_ok(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="success", records_replaced=12)

    cap = account_capability(db_session, account)

    assert cap["status"] == "CONNECTED_OK"
    assert cap["as_of"] is not None


def test_success_with_zero_rows_is_connected_empty_not_pending(db_session, make_user):
    """정상 조회·행 0건과 '한 번도 수집 안 함'은 다른 모양이어야 한다(QA-01)."""
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="success", records_replaced=0)

    cap = account_capability(db_session, account)

    assert cap["status"] == "CONNECTED_EMPTY"
    assert cap["status"] != "PENDING"
    assert cap["as_of"] is not None  # PENDING은 as_of가 None이지만 여기는 실제로 수집했다


def test_partial_success_is_connected_partial(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="partial_success", records_replaced=3)

    cap = account_capability(db_session, account)

    assert cap["status"] == "CONNECTED_PARTIAL"


def test_failed_permission_denied(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="failed", error_code="CLOUD_PERMISSION_DENIED")

    cap = account_capability(db_session, account)

    assert cap["status"] == "PERMISSION_DENIED"


def test_failed_setup_required(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="failed", error_code="COST_SETUP_REQUIRED")

    cap = account_capability(db_session, account)

    assert cap["status"] == "SETUP_REQUIRED"


def test_failed_rate_limited_is_collect_failed_not_permission_denied(db_session, make_user):
    """PROVIDER_RATE_LIMITED는 COLLECT_FAILED다 — PERMISSION_DENIED로 분류하면 사용자가
    권한 설정을 뒤지게 된다."""
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="failed", error_code="PROVIDER_RATE_LIMITED")

    cap = account_capability(db_session, account)

    assert cap["status"] == "COLLECT_FAILED"
    assert cap["status"] != "PERMISSION_DENIED"


def test_failed_unknown_error_code_is_collect_failed(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="failed", error_code="PROVIDER_API_ERROR")

    cap = account_capability(db_session, account)

    assert cap["status"] == "COLLECT_FAILED"


def test_running_keeps_previous_status_and_sets_ingestion_running(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="success", records_replaced=5, finished_delta_hours=2)
    # 이번 실행은 아직 진행 중
    running = CostIngestionRun(
        user_id=user.id, cloud_account_id=account.id, trigger_type="manual", status="running",
        period_start=dt.date(2026, 9, 1), period_end=dt.date(2026, 9, 18),
        requested_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(running)
    db_session.flush()

    cap = account_capability(db_session, account)

    assert cap["status"] == "CONNECTED_OK"  # 이전 성공 상태를 유지
    assert cap["ingestion_running"] is True  # 금액을 비우지 않고 진행 표시만 더한다


# --- partial_success는 "성공"으로 세지 않는다 (2026-09-23) ---------------------------------------
# 부분 응답은 `replace_cost_rows()` 전에 버려져 **행이 0건**이다(08 §4-4). 그런데 as_of·1시간 제한·
# covered_through가 partial_success를 성공으로 세고 있어, 아무것도 받지 못한 사용자가 "방금 수집됨"을
# 보고 1시간 동안 재시도조차 못 했다. 아래 3개가 그 회귀를 막는다.


def test_partial_success_does_not_update_as_of(db_session, make_user):
    """`as_of`/`last_success_at`은 저장된 데이터가 있는 시각이어야 한다 — partial run은 갱신하지 않는다.

    상태 자체는 CONNECTED_PARTIAL 그대로다(가장 최근 종결 run이 기준). 갱신되지 않아야 하는 것은
    "마지막 수집 시각"뿐이며, 그래야 지연(staleness) 판정도 어긋나지 않는다."""
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    success = _make_run(db_session, account, status="success", records_replaced=5, finished_delta_hours=50)
    _make_run(db_session, account, status="partial_success", error_code="PROVIDER_API_ERROR", finished_delta_hours=1)

    cap = account_capability(db_session, account)

    assert cap["status"] == "CONNECTED_PARTIAL"          # 최근 종결 run 기준 — 그대로
    assert cap["as_of"] == success.finished_at           # 50시간 전 성공 run
    assert cap["last_success_at"] == success.finished_at


def test_partial_success_only_leaves_no_success_time(db_session, make_user):
    """성공 이력이 한 번도 없고 partial만 있으면 '마지막 수집'은 비어 있어야 한다(0건을 수집으로 치지 않는다)."""
    user = make_user()
    account = _make_account(db_session, user)
    _make_credential(db_session, account)
    _make_run(db_session, account, status="partial_success", error_code="PROVIDER_RATE_LIMITED")

    cap = account_capability(db_session, account)

    assert cap["status"] == "CONNECTED_PARTIAL"
    assert cap["as_of"] is None
    assert cap["last_success_at"] is None
