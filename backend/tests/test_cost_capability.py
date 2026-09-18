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


def test_azure_is_unsupported_not_permission_denied(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user, provider="azure", external_account_id="sub-1")
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
