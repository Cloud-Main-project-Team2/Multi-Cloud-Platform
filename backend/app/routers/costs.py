"""비용 수집 실행 API 3종 + 조회 API 6종(docs/01_API_Specification_v1.2.md §11 — 제안, 팀
승인 전).

수집 실행 3종만 CSP API를 실제로 호출한다. 조회 6종은 `app/cost/query.py`·
`app/cost/capability.py`를 통해 **전부 DB만 읽는다** — 이 파일에 `boto3`/`azure`/`google`
import가 있는 건 수집 실행 부분뿐이어야 한다(§11-1-2 CSP 호출 경계, ADR-040).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.cost import COST_ADAPTERS, cost_source_for, is_cost_supported
from app.cost.coverage import resolve_basis
from app.cost.gating import manual_ingest_denial
from app.cost.capability import account_capability
from app.cost.ingest import AccountLockedError, finalize_interrupted_run, replace_cost_rows
from app.cost.notify import evaluate_for_account
from app.cost.review import evaluate_and_notify_for_account_safely
from app.cost.query import (
    CostQuery,
    breakdown,
    changes,
    collection_status,
    owned_accounts,
    parse_period,
    resolve_team_scope,
    staleness_threshold_hours,
    summary,
    trend,
)
from app.db import SessionLocal, get_db
from app.deps import get_current_user
from app.errors import ApiError, validation_error
from app.logging_config import log_background_task, log_business_event
from app.models import CloudAccount, Credential, CostIngestionRun, Notification, User
from app.providers.session import CredentialResolutionError, resolve_secret_payload
from app.schemas.costs import (
    CostBreakdownResponse,
    CostCapabilitiesData,
    CostCapabilitiesResponse,
    CostCapabilityItem,
    CostChangesResponse,
    CostCollectionStatusData,
    CostCollectionStatusItem,
    CostCollectionStatusResponse,
    CostIngestionRunCreateData,
    CostIngestionRunCreateItem,
    CostIngestionRunCreateRequest,
    CostIngestionRunCreateResponse,
    CostIngestionRunDetailResponse,
    CostIngestionRunListData,
    CostIngestionRunListResponse,
    CostIngestionRunOut,
    CostIngestionRunSkipped,
    CostSummaryResponse,
    CostTrendResponse,
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
    """1시간 제한의 기준이 되는 마지막 **성공** 수동 수집.

    ⚠️ partial_success는 제외한다(2026-09-23). 부분 응답은 저장 전에 버려져 **행이 0건**인데(08 §4-4),
    이것을 성공으로 세면 아무것도 받지 못한 사용자가 1시간 동안 재시도조차 못 한다 — 복구 수단이
    사라진다. 실패(failed)를 제한에 넣지 않는 것과 같은 이유다."""
    return (
        db.query(CostIngestionRun)
        .filter(
            CostIngestionRun.cloud_account_id == cloud_account_id,
            CostIngestionRun.trigger_type == "manual",
            CostIngestionRun.status == "success",
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


def _create_cost_ingestion_notification(
    db: Session, run: CostIngestionRun, account: CloudAccount | None
) -> None:
    """수동 새로고침(POST /cost-ingestion-runs) 종결 시 알림함에 완료 안내를 남긴다 — cost.js의
    폴링은 페이지에 종속돼(pagehide에서 중단) 화면을 벗어나면 끊기므로(item 2), 완료 시점을
    페이지와 무관하게 알리는 유일한 통로다. 자동 수집(app/cost/scheduler.py)은 별도 구현이라
    영향받지 않는다."""
    params: dict[str, Any] = {
        "run_id": str(run.id),
        "provider": account.provider if account else None,
        "account_name": account.account_label if account else None,
    }
    if run.status == "success":
        params["records_replaced"] = run.records_replaced
        notif_type, message_key = "cost_ingestion_succeeded", "notif.cost_ingestion.succeeded"
    else:
        # partial_success도 실패 쪽으로 묶는다 — 데이터가 조용히 덜 갱신된 채로 "성공" 배지가
        # 뜨면 사용자가 원인을 놓친다(§4-4 부분 응답 정책과 같은 방향).
        if run.error_message:
            params["reason"] = run.error_message
        elif run.error_code:
            params["reason"] = run.error_code
        notif_type, message_key = "cost_ingestion_failed", "notif.cost_ingestion.failed"
    db.add(
        Notification(
            user_id=run.user_id,
            type=notif_type,
            reference_type="cost_ingestion_run",
            reference_id=run.id,
            message_key=message_key,
            message_params=params,
        )
    )


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
            _create_cost_ingestion_notification(db, run, account)
            db.commit()
            return

        # 접수 시점에는 허용이었어도 실행 시점에 설정이 바뀌었을 수 있다(배포·env 변경). CSP를
        # 호출하기 전에 한 번 더 본다 — 이미 만들어진 run은 성공으로 두지 않고 실패로 종결한다.
        denial = manual_ingest_denial(account.provider, account.id)
        if denial is not None:
            run.status = "failed"
            run.error_code = denial
            run.error_message = "이 계정의 비용 수집이 현재 비활성화되어 있습니다."
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            _create_cost_ingestion_notification(db, run, account)
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
            _create_cost_ingestion_notification(db, run, account)
            db.commit()
            return

        try:
            secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
        except CredentialEncryptionError:
            run.status = "failed"
            run.error_code = "PROVIDER_API_ERROR"
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            _create_cost_ingestion_notification(db, run, account)
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
            _create_cost_ingestion_notification(db, run, account)
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
            _create_cost_ingestion_notification(db, run, account)
            db.commit()
            log_business_event(
                "cost.ingestion_run.finished", level="ERROR", run_id=run_id,
                cloud_account_id=account.id, provider=account.provider, status=run.status,
                error_code=run.error_code, api_calls=run.api_calls, records_replaced=0,
            )
            return

        try:
            replaced = replace_cost_rows(
                db, account, run, result.rows, source=cost_source_for(account.provider)
            )
        except AccountLockedError:
            db.rollback()
            run = db.get(CostIngestionRun, run_id)
            run.status = "failed"
            run.error_code = "JOB_ALREADY_RUNNING"
            run.error_message = "이 계정의 비용 수집이 이미 진행 중입니다."
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            _create_cost_ingestion_notification(db, run, account)
            db.commit()
            return

        run.records_replaced = replaced
        run.status = "success"
        # 행 교체와 **같은 트랜잭션**에 근거를 남긴다 — 저장 결과와 근거가 어긋나지 않게(A-2).
        run.coverage_basis = resolve_basis(result.coverage_basis, account.provider)
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        _create_cost_ingestion_notification(db, run, account)
        db.commit()

        log_business_event(
            "cost.ingestion_run.finished", level="INFO", run_id=run_id,
            cloud_account_id=account.id, provider=account.provider, status=run.status,
            api_calls=run.api_calls, records_replaced=replaced,
        )
        # 수집이 커밋된 뒤 그 계정의 팀 예산 임계(80/100%)를 평가한다(PR 7). 실패는 로그만 —
        # 수집 결과에는 영향이 없다.
        evaluate_for_account(db, account)
        # 급증 탐지(PR 8) — 이번 run 범위가 아니라 저장된 판정 대상 날 전부를 본다.
        evaluate_and_notify_for_account_safely(db, account)
    except Exception:       # noqa: BLE001 — 기록만 남기고 그대로 올린다(로그는 log_background_task가 찍는다)
        # 여기서 잡지 않으면 run이 'running'에 박혀 그 계정은 이후 수집이 영구히
        # JOB_ALREADY_RUNNING으로 막힌다(_last_active_run). 상태를 종결로 남긴다.
        finalize_interrupted_run(db, run_id)
        raise
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
        # 구현 지원(위)과 수집 활성화(아래)는 다른 질문이다 — 꺼져 있다고 UNSUPPORTED나
        # PERMISSION_DENIED로 위장하지 않는다(app/cost/gating.py). 계정 id를 준 요청이든 생략한
        # 요청이든 같은 목록을 지나므로 진입 경로가 달라도 판정은 하나다.
        denial = manual_ingest_denial(account.provider, account.id)
        if denial is not None:
            skipped.append(
                CostIngestionRunSkipped(cloud_account_id=str_id(account.id), reason_code=denial)
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


# --- 조회 6종(PR 5) — 전부 DB만 읽는다 ------------------------------------------------------


def _build_query(
    period_start: str | None,
    period_end: str | None,
    provider: list[str],
    cloud_account_id: list[str],
    currency: str | None,
    charge_category: list[str],
    *,
    team_id: list[str] | None = None,
    db: Session | None = None,
    user_id: int | None = None,
) -> CostQuery:
    start, end = parse_period(period_start, period_end)
    account_ids = _parse_int_list(cloud_account_id, "cloud_account_id") if cloud_account_id else []
    if team_id:
        # 팀 필터(§4 공통 query, CFL-03)는 계정 집합으로 풀어서 넣는다 — query.py의 필터 지점
        # 18곳을 전부 고치지 않기 위해서다. "unassigned"는 team_id IS NULL.
        account_ids = resolve_team_scope(db, user_id, team_id, account_ids)
    return CostQuery(
        period_start=start, period_end=end, providers=provider, cloud_account_ids=account_ids,
        currency=currency, charge_categories=charge_category or ["usage"],
    )


@router.get("/costs/capabilities", response_model=CostCapabilitiesResponse)
def get_cost_capabilities(
    provider: list[str] = Query(default=[]),
    cloud_account_id: list[str] = Query(default=[]),
    team_id: list[str] = Query(default=[]),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CostCapabilitiesResponse:
    q = _build_query(None, None, provider, cloud_account_id, None, [], team_id=team_id, db=db, user_id=current_user.id)
    accounts = owned_accounts(db, current_user.id, q)

    items = []
    for account in accounts:
        cap = account_capability(db, account)
        items.append(
            CostCapabilityItem(
                cloud_account_id=str_id(account.id), provider=account.provider,
                external_account_id=account.external_account_id, account_label=account.account_label,
                team_id=str_id(account.team_id), status=cap["status"], as_of=iso_z(cap["as_of"]),
                ingestion_running=cap["ingestion_running"], cost_read=cap["cost_read"],
                capability_source=cap["capability_source"], currency=cap["currency"],
                setup_hint=cap["setup_hint"], last_error_code=cap["last_error_code"],
            )
        )
    return CostCapabilitiesResponse(
        data=CostCapabilitiesData(
            staleness_threshold_hours=staleness_threshold_hours(), items=items, total=len(items)
        )
    )


def _iso_z_fields(d: dict, fields: tuple[str, ...]) -> dict:
    out = dict(d)
    for f in fields:
        if f in out:
            out[f] = iso_z(out[f])
    return out


@router.get("/costs/summary", response_model=CostSummaryResponse)
def get_cost_summary(
    period_start: str | None = None,
    period_end: str | None = None,
    provider: list[str] = Query(default=[]),
    cloud_account_id: list[str] = Query(default=[]),
    team_id: list[str] = Query(default=[]),
    currency: str | None = None,
    charge_category: list[str] = Query(default=[]),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CostSummaryResponse:
    q = _build_query(period_start, period_end, provider, cloud_account_id, currency, charge_category, team_id=team_id, db=db, user_id=current_user.id)
    data = summary(db, current_user.id, q)
    data["as_of"] = iso_z(data["as_of"])
    data["accounts"] = [_iso_z_fields(a, ("as_of", "resources_synced_at")) for a in data["accounts"]]
    return CostSummaryResponse(data=data)


@router.get("/costs/trend", response_model=CostTrendResponse)
def get_cost_trend(
    period_start: str | None = None,
    period_end: str | None = None,
    provider: list[str] = Query(default=[]),
    cloud_account_id: list[str] = Query(default=[]),
    team_id: list[str] = Query(default=[]),
    currency: str | None = None,
    charge_category: list[str] = Query(default=[]),
    granularity: str = "daily",
    group_by: str = "provider",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CostTrendResponse:
    if granularity not in ("daily", "weekly", "monthly", "quarterly"):
        raise validation_error("granularity는 daily|weekly|monthly|quarterly 중 하나여야 합니다.")
    q = _build_query(period_start, period_end, provider, cloud_account_id, currency, charge_category, team_id=team_id, db=db, user_id=current_user.id)
    data = trend(db, current_user.id, q, granularity, group_by, currency)
    data.update({"granularity": granularity, "group_by": group_by, "staleness_threshold_hours": staleness_threshold_hours()})
    return CostTrendResponse(data=data)


@router.get("/costs/breakdown", response_model=CostBreakdownResponse)
def get_cost_breakdown(
    period_start: str | None = None,
    period_end: str | None = None,
    provider: list[str] = Query(default=[]),
    cloud_account_id: list[str] = Query(default=[]),
    team_id: list[str] = Query(default=[]),
    currency: str | None = None,
    charge_category: list[str] = Query(default=[]),
    dimension: str = "provider",
    top_n: int = 6,
    tag_key: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CostBreakdownResponse:
    if dimension == "tag":
        # 태그 배분은 설계만(확정 7) — 501로 응답한다.
        raise ApiError(501, "UNSUPPORTED_OPERATION", "태그 기준 비용 배분은 아직 지원하지 않습니다.")
    if dimension not in ("provider", "service", "category", "account", "team"):
        raise validation_error("dimension이 올바르지 않습니다.")
    top_n = max(1, min(top_n, 20))
    q = _build_query(period_start, period_end, provider, cloud_account_id, currency, charge_category, team_id=team_id, db=db, user_id=current_user.id)
    data = breakdown(db, current_user.id, q, dimension, top_n, currency)
    return CostBreakdownResponse(data=data)


@router.get("/costs/changes", response_model=CostChangesResponse)
def get_cost_changes(
    period_start: str | None = None,
    period_end: str | None = None,
    provider: list[str] = Query(default=[]),
    cloud_account_id: list[str] = Query(default=[]),
    team_id: list[str] = Query(default=[]),
    currency: str | None = None,
    charge_category: list[str] = Query(default=[]),
    compare: str = "previous_period",
    dimension: str = "service",
    top_n: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CostChangesResponse:
    if compare not in ("previous_period", "previous_month"):
        raise validation_error("compare는 previous_period|previous_month 중 하나여야 합니다.")
    top_n = max(1, min(top_n, 50))
    # currency는 공통 query(05 §4 · 08 §5-2)다 — 이전엔 None 고정이라 '청구 통화' 필터가 비교에만 안 먹었다.
    q = _build_query(period_start, period_end, provider, cloud_account_id, currency, charge_category, team_id=team_id, db=db, user_id=current_user.id)
    data = changes(db, current_user.id, q, compare, dimension, top_n, currency_param=currency)
    return CostChangesResponse(data=data)


@router.get("/costs/collection-status", response_model=CostCollectionStatusResponse)
def get_cost_collection_status(
    period_start: str | None = None,
    period_end: str | None = None,
    provider: list[str] = Query(default=[]),
    cloud_account_id: list[str] = Query(default=[]),
    team_id: list[str] = Query(default=[]),
    currency: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CostCollectionStatusResponse:
    # 공통 query 7개를 받는다(05 §4 "6종 모두 같다"). period·currency는 계정별 coverage(조회 기간 기준
    # 결측일)를 내는 데 쓰인다 — 안 넘기면 이번 달·전체 통화 기준이다.
    q = _build_query(period_start, period_end, provider, cloud_account_id, currency, [], team_id=team_id, db=db, user_id=current_user.id)
    raw_items = collection_status(db, current_user.id, q)
    items = [
        CostCollectionStatusItem(
            cloud_account_id=str_id(item["cloud_account_id"]), provider=item["provider"], status=item["status"],
            as_of=iso_z(item["as_of"]), ingestion_running=item["ingestion_running"],
            last_success_at=iso_z(item["last_success_at"]), last_attempt_at=iso_z(item["last_attempt_at"]),
            last_error_code=item["last_error_code"], next_manual_allowed_at=iso_z(item["next_manual_allowed_at"]),
            covered_through=item["covered_through"], missing_days=item["missing_days"],
            missing_count=item["missing_count"], coverage=item["coverage"],
        )
        for item in raw_items
    ]
    return CostCollectionStatusResponse(
        data=CostCollectionStatusData(
            staleness_threshold_hours=staleness_threshold_hours(), items=items, total=len(items)
        )
    )
