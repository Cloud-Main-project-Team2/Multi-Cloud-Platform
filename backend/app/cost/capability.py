"""`GET /costs/capabilities` 상태 판정 — 이 CSP에서 비용을 볼 수 있나. **CSP를 호출하지
않는다**(ADR-040) — 이 파일에 `boto3`/`azure`/`google` import가 있으면 잘못된 것이다.

판정 순서와 `error_code` → `status` 매핑은 docs/01_API_Specification_v1.2.md §11-3(=05
API계약.md §4-1)이 정본이다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.cost import is_cost_supported
from app.models import CloudAccount, CostIngestionRun, Credential

_TERMINAL_STATUSES = ("success", "partial_success", "failed", "cancelled")
_ACTIVE_STATUSES = ("pending", "running")

# 마지막 완료 run의 error_code -> status. PROVIDER_RATE_LIMITED는 COLLECT_FAILED다 —
# PERMISSION_DENIED로 분류하면 사용자가 권한 설정을 뒤지게 된다(없는 죄를 씌운다).
_ERROR_CODE_TO_STATUS = {
    "CLOUD_PERMISSION_DENIED": "PERMISSION_DENIED",
    "COST_SETUP_REQUIRED": "SETUP_REQUIRED",
}


def account_capability(db: Session, account: CloudAccount) -> dict:
    """반환 필드: status·as_of·ingestion_running·cost_read·capability_source·currency·
    setup_hint·last_error_code·last_success_at(내부용, 응답 직렬화 시 필요한 쪽만 쓴다)."""
    if not is_cost_supported(account.provider):
        return {
            "status": "UNSUPPORTED",
            "as_of": None,
            "ingestion_running": False,
            "cost_read": None,
            "capability_source": "not_implemented",
            "currency": None,
            "setup_hint": f"{account.provider.upper()} 비용 수집은 아직 구현되지 않았습니다.",
            "last_error_code": None,
            "last_success_at": None,
        }

    # app/providers/{azure,gcp}.py의 permission_scope.cost_read는 하드코딩 false다 — 여기까지
    # 오지 않는다(위에서 UNSUPPORTED로 이미 끝났다). 그 값을 그대로 썼다면 Azure·GCP가
    # PERMISSION_DENIED로 보였을 것이다.
    credential = (
        db.query(Credential)
        .filter(Credential.cloud_account_id == account.id, Credential.verified.is_(True))
        .order_by(Credential.display_order, Credential.id)
        .first()
    )

    ingestion_running = (
        db.query(CostIngestionRun)
        .filter(
            CostIngestionRun.cloud_account_id == account.id,
            CostIngestionRun.status.in_(_ACTIVE_STATUSES),
        )
        .first()
        is not None
    )

    # ⚠️ partial_success는 "성공"이 아니다 — 부분 응답은 `replace_cost_rows()`를 부르기 전에 버려지므로
    # 그 run은 **행을 하나도 저장하지 않는다**(routers/costs.py·cost/scheduler.py, 08 §4-4). 여기에
    # partial_success를 넣으면 `as_of`/`last_success_at`이 "데이터가 없는 시각"을 가리켜 화면의 "마지막
    # 수집"이 방금으로 보이고 지연(36시간) 판정도 최신으로 어긋난다. coverage(cost/coverage.py)는 이미
    # status == "success"만 인정하므로 여기서도 같은 기준을 쓴다(2026-09-23).
    last_success = (
        db.query(CostIngestionRun)
        .filter(
            CostIngestionRun.cloud_account_id == account.id,
            CostIngestionRun.status == "success",
        )
        .order_by(CostIngestionRun.finished_at.desc())
        .first()
    )

    if credential is None:
        return {
            "status": "NOT_CONNECTED",
            "as_of": last_success.finished_at if last_success else None,
            "ingestion_running": ingestion_running,
            "cost_read": False,
            "capability_source": "probed",
            "currency": None,
            "setup_hint": None,
            "last_error_code": None,
            "last_success_at": last_success.finished_at if last_success else None,
        }

    last_terminal = (
        db.query(CostIngestionRun)
        .filter(
            CostIngestionRun.cloud_account_id == account.id,
            CostIngestionRun.status.in_(_TERMINAL_STATUSES),
        )
        .order_by(CostIngestionRun.requested_at.desc())
        .first()
    )

    if last_terminal is None:
        status = "PENDING"
    elif last_terminal.status == "success":
        status = "CONNECTED_OK" if last_terminal.records_replaced > 0 else "CONNECTED_EMPTY"
    elif last_terminal.status == "partial_success":
        status = "CONNECTED_PARTIAL"
    elif last_terminal.status == "failed":
        if last_terminal.error_code == "PROVIDER_RATE_LIMITED":
            status = "COLLECT_FAILED"
        else:
            status = _ERROR_CODE_TO_STATUS.get(last_terminal.error_code, "COLLECT_FAILED")
    else:  # cancelled — 한 번도 완주하지 못했으니 PENDING으로 본다
        status = "PENDING"

    cost_read = credential.permission_scope.get("cost_read", False) if credential.permission_scope else False

    return {
        "status": status,
        "as_of": last_success.finished_at if last_success else None,
        "ingestion_running": ingestion_running,
        "cost_read": bool(cost_read),
        "capability_source": "probed",
        "currency": _account_currency(db, account),
        "setup_hint": None,
        "last_error_code": last_terminal.error_code if last_terminal else None,
        "last_success_at": last_success.finished_at if last_success else None,
    }


def _account_currency(db: Session, account: CloudAccount) -> str | None:
    """이 계정에서 마지막으로 확인된 청구 통화 — 가장 최근 cloud_account_costs 행의 currency."""
    from app.models import CloudAccountCost

    row = (
        db.query(CloudAccountCost.currency)
        .filter(CloudAccountCost.cloud_account_id == account.id)
        .order_by(CloudAccountCost.as_of.desc())
        .first()
    )
    return row[0] if row else None


def staleness_text_hint(as_of: dt.datetime | None, threshold_hours: int) -> bool:
    """as_of가 임계를 넘었으면 True. 화면 문구는 만들지 않는다(프론트 cost-state.js 몫) —
    여기서는 API가 필요하면 쓸 수 있는 boolean 판정만 제공한다."""
    if as_of is None:
        return False
    now = dt.datetime.now(dt.timezone.utc)
    hours = (now - as_of).total_seconds() / 3600
    return hours >= threshold_hours
