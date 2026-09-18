"""비용 수집 실행 API 3종(docs/01_API_Specification_v1.2.md §11-9 — 제안, 팀 승인 전).

CSP API를 실제로 호출하는 유일한 경로다. 비용 조회 6종(PR 5)은 전부 DB만 읽는다 — 이
파일에 `boto3`/`azure`/`google` import가 있는 건 여기뿐이어야 한다(§11-1-2 CSP 호출 경계).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.cost import COST_ADAPTERS, is_cost_supported
from app.cost.ingest import AccountLockedError, replace_cost_rows
from app.db import SessionLocal, get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.logging_config import log_background_task, log_business_event
from app.models import CloudAccount, Credential, CostIngestionRun, User
from app.providers.session import CredentialResolutionError, resolve_secret_payload
from app.schemas.costs import (
    CostIngestionRunCreateData,
    CostIngestionRunCreateItem,
    CostIngestionRunCreateRequest,
    CostIngestionRunCreateResponse,
    CostIngestionRunDetailResponse,
    CostIngestionRunListData,
    CostIngestionRunListResponse,
    CostIngestionRunOut,
    CostIngestionRunSkipped,
)
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["costs"])

_MANUAL_RATE_LIMIT = dt.timedelta(hours=1)
_MAX_ACCOUNT_IDS = 20


def _parse_int_list(values: list[str], field: str) -> list[int]:
    try:
        return [int(v) for v in values]
    except ValueError as exc:
        raise validation_error(
            f"{field}는 숫자 ID여야 합니다.", details=[{"field": field, "reason": "invalid"}]
        ) from exc


def _default_period() -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    return today.replace(day=1), today + dt.timedelta(days=1)


def _last_active_run(db: Session, cloud_account_id: int) -> CostIngestionRun | None:
    return (
        db.query(CostIngestionRun)
        .filter(
            CostIngestionRun.cloud_account_id == cloud_account_id,
            CostIngestionRun.status.in_(("pending", "running")),
        )
        .order_by(CostIngestionRun.requested_at.desc())
        .first()
    )


def _last_manual_success(db: Session, cloud_account_id: int) -> CostIngestionRun | None:
    return (
        db.query(CostIngestionRun)
        .filter(
            CostIngestionRun.cloud_account_id == cloud_account_id,
            CostIngestionRun.trigger_type == "manual",
            CostIngestionRun.status.in_(("success", "partial_success")),
        )
        .order_by(CostIngestionRun.finished_at.desc())
        .first()
    )


def _serialize_run(run: CostIngestionRun, provider: str) -> CostIngestionRunOut:
    return CostIngestionRunOut(
        id=str_id(run.id),
        cloud_account_id=str_id(run.cloud_account_id),
        provider=provider,
        trigger_type=run.trigger_type,
        status=run.status,
        period_start=run.period_start.isoformat(),
        period_end=run.period_end.isoformat(),
        requested_at=iso_z(run.requested_at),
        started_at=iso_z(run.started_at),
        finished_at=iso_z(run.finished_at),
        api_calls=run.api_calls,
        records_replaced=run.records_replaced,
        error_code=run.error_code,
        error_message=run.error_message,
    )


# --- 실행(백그라운드) ---------------------------------------------------------------------


def _run_cost_ingestion_run(run_id: int) -> None:
    # 요청 사이클 밖(BackgroundTasks)이라 전역 예외 핸들러가 닿지 않는다 — 감싸지 않으면
    # 수집 도중 터진 예외가 로그에 한 줄도 남지 않는다(sync_jobs.py와 동일 원칙).
    with log_background_task("cost.ingestion_run", run_id=run_id):
        _run_cost_ingestion_run_inner(run_id)


def _run_cost_ingestion_run_inner(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(CostIngestionRun, run_id)
        if run is None:
            return

        account = db.get(CloudAccount, run.cloud_account_id)
        adapter_cls = COST_ADAPTERS.get(account.provider) if account else None
        if account is None or adapter_cls is None:
            run.status = "failed"
            run.error_code = "UNSUPPORTED"
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
            return

        run.status = "running"
        run.started_at = dt.datetime.now(dt.timezone.utc)
        db.commit()

        credential = (
            db.query(Credential)
            .filter(Credential.cloud_account_id == account.id, Credential.verified.is_(True))
            .order_by(Credential.display_order, Credential.id)
            .first()
        )
        if credential is None:
            run.status = "failed"
            run.error_code = "CLOUD_PERMISSION_DENIED"
            run.error_message = "검증된 자격 증명이 없습니다."
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
            return

        try:
            secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
        except CredentialEncryptionError:
            run.status = "failed"
            run.error_code = "PROVIDER_API_ERROR"
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
            return

        # 위임(assume_role) credential이면 임시 자격증명을 발급받는다(레거시는 그대로 통과) —
        # app/routers/sync_jobs.py와 같은 지점, 같은 패턴.
        try:
            secret_payload = resolve_secret_payload(
                account.provider, secret_payload, credential_id=credential.id
            )
        except CredentialResolutionError as exc:
            run.status = "failed"
            run.error_code = exc.error_code
            run.error_message = exc.message
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
            return

        try:
            result = adapter_cls().fetch(
                secret_payload, account.external_account_id, run.period_start, run.period_end
            )
        finally:
            del secret_payload

        run.api_calls = result.api_calls

        if result.partial:
            # 부분 응답으로 전체를 갈아치우지 않는다 — 조용히 금액이 줄어드는 것을 막는다
            # (08_백엔드_구현가이드.md §4-4).
            run.status = "partial_success"
            run.error_code = result.error_code
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
            log_business_event(
                "cost.ingestion_run.finished", level="ERROR", run_id=run_id,
                cloud_account_id=account.id, provider=account.provider, status=run.status,
                error_code=run.error_code, api_calls=run.api_calls, records_replaced=0,
            )
            return

        try:
            replaced = replace_cost_rows(
                db, account, run, result.rows, source=f"{account.provider}_cost_explorer"
            )
        except AccountLockedError:
            db.rollback()
            run = db.get(CostIngestionRun, run_id)
            run.status = "failed"
            run.error_code = "JOB_ALREADY_RUNNING"
            run.error_message = "이 계정의 비용 수집이 이미 진행 중입니다."
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
            return

        run.records_replaced = replaced
        run.status = "success"
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()

        log_business_event(
            "cost.ingestion_run.finished", level="INFO", run_id=run_id,
            cloud_account_id=account.id, provider=account.provider, status=run.status,
            api_calls=run.api_calls, records_replaced=replaced,
        )
    finally:
        db.close()


# --- 엔드포인트 --------------------------------------------------------------------------


@router.post("/cost-ingestion-runs", status_code=202, response_model=CostIngestionRunCreateResponse)
def create_cost_ingestion_runs(
    payload: CostIngestionRunCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CostIngestionRunCreateResponse:
    period_start, period_end = _default_period()
    if payload.period_start:
        period_start = dt.date.fromisoformat(payload.period_start)
    if payload.period_end:
        period_end = dt.date.fromisoformat(payload.period_end)
    if period_end <= period_start:
        raise validation_error(
            "period_end는 period_start보다 커야 합니다.",
            details=[{"field": "period_end", "reason": "invalid_range"}],
        )

    if payload.cloud_account_ids is None:
        accounts = db.query(CloudAccount).filter(CloudAccount.user_id == current_user.id).all()
    else:
        if len(payload.cloud_account_ids) > _MAX_ACCOUNT_IDS:
            raise validation_error(
                f"cloud_account_ids는 최대 {_MAX_ACCOUNT_IDS}개입니다.",
                details=[{"field": "cloud_account_ids", "reason": "too_many"}],
            )
        ids = _parse_int_list(payload.cloud_account_ids, "cloud_account_ids")
        accounts = (
            db.query(CloudAccount)
            .filter(CloudAccount.id.in_(ids), CloudAccount.user_id == current_user.id)
            .all()
        )
        if len(accounts) != len(set(ids)):
            raise ApiError(404, "CLOUD_ACCOUNT_NOT_FOUND", "클라우드 계정을 찾을 수 없습니다.")

    if not accounts:
        raise validation_error("수집할 클라우드 계정이 없습니다.")

    now = dt.datetime.now(dt.timezone.utc)
    items: list[CostIngestionRunCreateItem] = []
    skipped: list[CostIngestionRunSkipped] = []

    for account in accounts:
        if _last_active_run(db, account.id) is not None:
            skipped.append(
                CostIngestionRunSkipped(cloud_account_id=str_id(account.id), reason_code="JOB_ALREADY_RUNNING")
            )
            continue
        if not is_cost_supported(account.provider):
            skipped.append(
                CostIngestionRunSkipped(cloud_account_id=str_id(account.id), reason_code="UNSUPPORTED")
            )
            continue
        last_success = _last_manual_success(db, account.id)
        if last_success is not None and last_success.finished_at is not None:
            next_allowed = last_success.finished_at + _MANUAL_RATE_LIMIT
            if now < next_allowed:
                skipped.append(
                    CostIngestionRunSkipped(
                        cloud_account_id=str_id(account.id),
                        reason_code="RATE_LIMITED",
                        next_allowed_at=iso_z(next_allowed),
                    )
                )
                continue

        run = CostIngestionRun(
            user_id=current_user.id,
            cloud_account_id=account.id,
            trigger_type="manual",
            status="pending",
            period_start=period_start,
            period_end=period_end,
            requested_at=now,
        )
        db.add(run)
        db.flush()
        items.append(
            CostIngestionRunCreateItem(
                id=str_id(run.id),
                cloud_account_id=str_id(account.id),
                status=run.status,
                status_url=f"/api/v1/cost-ingestion-runs/{run.id}",
            )
        )

    if not items:
        reasons = {s.reason_code for s in skipped}
        if reasons == {"RATE_LIMITED"}:
            db.rollback()
            raise ApiError(429, "RATE_LIMITED", "요청한 계정이 모두 1시간 제한에 걸렸습니다.")
        if reasons == {"JOB_ALREADY_RUNNING"}:
            db.rollback()
            raise ApiError(409, "JOB_ALREADY_RUNNING", "요청한 계정이 모두 이미 수집 중입니다.")

    db.commit()
    for item in items:
        background_tasks.add_task(_run_cost_ingestion_run, int(item.id))

    return CostIngestionRunCreateResponse(data=CostIngestionRunCreateData(items=items, skipped=skipped))


@router.get("/cost-ingestion-runs", response_model=CostIngestionRunListResponse)
def list_cost_ingestion_runs(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CostIngestionRunListResponse:
    rows = (
        db.query(CostIngestionRun, CloudAccount.provider)
        .join(CloudAccount, CloudAccount.id == CostIngestionRun.cloud_account_id)
        .filter(CostIngestionRun.user_id == current_user.id)
        .order_by(CostIngestionRun.requested_at.desc())
        .all()
    )
    items = [_serialize_run(run, provider) for run, provider in rows]
    return CostIngestionRunListResponse(data=CostIngestionRunListData(items=items, total=len(items)))


@router.get("/cost-ingestion-runs/{run_id}", response_model=CostIngestionRunDetailResponse)
def get_cost_ingestion_run(
    run_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CostIngestionRunDetailResponse:
    try:
        parsed_id = int(run_id)
    except ValueError as exc:
        raise ApiError(404, "COST_INGESTION_RUN_NOT_FOUND", "수집 실행을 찾을 수 없습니다.") from exc

    row = (
        db.query(CostIngestionRun, CloudAccount.provider)
        .join(CloudAccount, CloudAccount.id == CostIngestionRun.cloud_account_id)
        .filter(CostIngestionRun.id == parsed_id, CostIngestionRun.user_id == current_user.id)
        .one_or_none()
    )
    if row is None:
        raise ApiError(404, "COST_INGESTION_RUN_NOT_FOUND", "수집 실행을 찾을 수 없습니다.")

    run, provider = row
    return CostIngestionRunDetailResponse(data=_serialize_run(run, provider))
