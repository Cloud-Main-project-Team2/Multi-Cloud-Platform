"""§10 프로비저닝 API — provider별 개별 엔드포인트(`/provisioning/{provider}/{service}`).

## 통합 설계(2026-09-11)

AWS·Azure·GCP를 한 사람씩 따로 구현하며 아키텍처가 갈라졌던 것을 하나로 합쳤다. 러너는
`app/provisioning.py` 레지스트리가 `(provider, service_code)`로 반환하는 **얇은 모듈**이고,
각 러너는 두 함수 + 한 상수만 노출한다:

- `validate_spec(common_spec, provider_spec)` — 동기, 요청 처리 중 호출, 실패 시 `ApiError` raise
- `run(*, job_id, workspace_dir, project_id, common_spec, provider_spec, secret_payload,
   cancel_check) -> TerraformResult` — 비동기 백그라운드, raise하지 않음
- `SENSITIVE_PROVIDER_SPEC_FIELDS: frozenset[str]` — secret-필드 금지 검사 예외(예: Azure
  `admin_password` = 만들어질 VM의 OS 비밀번호. CSP 자격증명이 아니므로 허용하되 DB엔 안 남긴다)

job 생명주기(상태 전이·감사·알림·리소스행 생성·취소)는 **전부 이 라우터가 중앙에서** 처리한다 —
러너는 Terraform 실행 결과(`TerraformResult`)만 돌려준다. `sync_jobs.py`와 같은 이유로 백그라운드
실행 함수(`_run_provisioning_job`)는 `SessionLocal()`을 열고, 테스트는 `_execute_job()`을
`db_session`으로 직접 호출해 검증한다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_current_user, require_confirmation
from app.errors import ApiError
from app.models import CloudAccount, Credential, Notification, ProvisioningJob, Resource, ServiceCatalog, User
from app.pricing import estimate_monthly_cost_usd
from app.provisioning import get_runner
from app.schemas.provisioning import (
    CreateProvisioningJobRequest,
    ProvisioningJobCreateData,
    ProvisioningJobCreateResponse,
    ProvisioningJobError,
    ProvisioningJobListData,
    ProvisioningJobListResponse,
    ProvisioningJobOut,
    ProvisioningJobResponse,
)
from app.security.credential_crypto import CredentialEncryptionError, decrypt_credential_json
from app.serialization import iso_z, str_id

router = APIRouter(prefix="/api/v1", tags=["provisioning"])

# best-effort 취소(비영속). sync_jobs.py의 `_CANCEL_REQUESTED`와 같은 정책 — 프로세스 메모리에만
# 있어 재시작 시 사라지고, 이미 시작된 terraform 단계 하나는 끝까지 진행된다.
_CANCEL_REQUESTED: set[int] = set()

# 요청 spec에 섞여 오면 거부할 민감 키 이름(§10.3 SECRET_FIELD_NOT_ALLOWED). 러너가 선언한
# SENSITIVE_PROVIDER_SPEC_FIELDS(예: azure admin_password)는 예외로 통과시킨다.
_SECRET_FIELD_DENYLIST = {
    "secret", "secret_payload", "password", "access_key_id", "secret_access_key",
    "session_token", "client_secret", "private_key", "private_key_id", "token",
    "authorization", "api_key", "credentials",
}


# --- 파싱/소유권 헬퍼 -----------------------------------------------------------------------


def _parse_id(raw: str, code: str, message: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(404, code, message) from exc


def _find_secret_field(spec: dict[str, Any], prefix: str, allow: frozenset[str]) -> str | None:
    for key, value in spec.items():
        if key in allow:
            continue
        if key.lower() in _SECRET_FIELD_DENYLIST:
            return f"{prefix}.{key}"
        if isinstance(value, dict):
            nested = _find_secret_field(value, f"{prefix}.{key}", allow)
            if nested:
                return nested
    return None


def _strip_sensitive(provider_spec: dict[str, Any], sensitive: frozenset[str]) -> dict[str, Any]:
    """DB(`spec_json`)에 저장하기 전에 러너가 선언한 민감 필드를 제거한다."""
    return {k: v for k, v in provider_spec.items() if k not in sensitive}


def _get_owned_credential(db: Session, user_id: int, credential_id: str) -> tuple[Credential, CloudAccount]:
    cred_id = _parse_id(credential_id, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
    row = (
        db.query(Credential, CloudAccount)
        .join(CloudAccount, Credential.cloud_account_id == CloudAccount.id)
        .filter(Credential.id == cred_id, CloudAccount.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
    return row


def _get_owned_job(db: Session, user_id: int, raw_id: str) -> ProvisioningJob:
    job = db.get(ProvisioningJob, _parse_id(raw_id, "PROVISIONING_JOB_NOT_FOUND", "프로비저닝 작업을 찾을 수 없습니다."))
    if job is None or job.user_id != user_id:
        raise ApiError(404, "PROVISIONING_JOB_NOT_FOUND", "프로비저닝 작업을 찾을 수 없습니다.")
    return job


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# --- 직렬화 ---------------------------------------------------------------------------------


def _serialize_job(job: ProvisioningJob) -> ProvisioningJobOut:
    spec = job.spec_json or {}
    error = ProvisioningJobError(code=job.error_code, message=job.error_message) if job.error_code else None
    return ProvisioningJobOut(
        id=str_id(job.id),
        credential_id=str_id(job.credential_id),
        service_catalog_id=str_id(job.service_catalog_id),
        workspace_name=job.workspace_name,
        common_spec=spec.get("common_spec", {}),
        provider_spec=spec.get("provider_spec", {}),
        status=job.status,
        progress_percent=job.progress_percent,
        created_resource_count=job.created_resource_count,
        result=job.result_json,
        error=error,
        created_at=iso_z(job.created_at),
        started_at=iso_z(job.started_at),
        finished_at=iso_z(job.finished_at),
    )


# --- 리소스행/알림/종결 (provider 무관, 라우터 중앙 처리) --------------------------------------


def _resource_attrs(
    provider: str, service_code: str, outputs: dict, common_spec: dict, provider_spec: dict
) -> tuple[str | None, str, str | None, str | None]:
    """terraform outputs → `(external_resource_id, original_resource_type, region, name)`.
    provider(+service_code)마다 output 키·리소스 유형·region 파생 방식이 다르다.
    external_resource_id가 없으면(output 누락) 첫 원소가 None이고, 라우터는 리소스행을 만들지 않는다.
    """
    if provider == "aws" and service_code == "s3":
        # app/providers/aws.py의 discover_resources()/perform_resource_action()과 같은 관례
        # (original_resource_type="S3 Bucket", region=None — S3는 전역 서비스라 동기화 시
        # 리전을 조회하지 않는다) — 다음 동기화 때 같은 Resource 행으로 합쳐지도록 맞춘다.
        bucket_name = outputs.get("bucket_name")
        return bucket_name, "S3 Bucket", None, bucket_name
    if provider == "aws" and service_code == "cloudfront":
        # CloudFront도 S3처럼 전역 서비스라 region=None. discover_resources()에 아직 CloudFront가
        # 없어(app/providers/aws.py) 강제할 기존 관례는 없지만, "{Service} {유형}" 표기(S3
        # Bucket/RDS Instance)를 그대로 따른다.
        distribution_id = outputs.get("distribution_id")
        return distribution_id, "CloudFront Distribution", None, common_spec.get("name")
    if provider == "aws" and service_code == "rds":
        # app/providers/aws.py의 discover_resources()/perform_resource_action()과 같은 관례
        # (original_resource_type="RDS Instance", name=DBInstanceIdentifier — RDS는 Name 태그
        # 개념이 없어 discover도 식별자를 그대로 표시 이름으로 쓴다).
        db_instance_id = outputs.get("db_instance_id")
        return db_instance_id, "RDS Instance", provider_spec.get("region"), db_instance_id
    if provider == "aws":
        return outputs.get("instance_id"), "AWS::EC2::Instance", provider_spec.get("region"), common_spec.get("name")
    if provider == "gcp" and service_code == "cloud_sql":
        instance_name = outputs.get("instance_name")
        region = outputs.get("region") or provider_spec.get("region")
        return instance_name, "Cloud SQL Instance", region, instance_name
    if provider == "gcp" and service_code == "cloud_storage":
        bucket_name = outputs.get("bucket_name")
        region = outputs.get("region") or provider_spec.get("region")
        return bucket_name, "Cloud Storage Bucket", region, bucket_name
    if provider == "gcp" and service_code == "cloud_cdn":
        # S3/CloudFront와 같은 이유로 region=None — 로드밸런서 스택 전체가 전역(global) 리소스다.
        forwarding_rule_name = outputs.get("forwarding_rule_name")
        return forwarding_rule_name, "Cloud CDN (HTTP LB)", None, forwarding_rule_name
    if provider == "gcp":
        instance_name = outputs.get("instance_name")
        # resources.region엔 GCP zone을 그대로 저장한다(app/providers/gcp.py의
        # perform_resource_action/discover_resources와 동일 관례, CLAUDE.md "GCP zone 단순화"
        # 참고) — region prefix로 잘라 저장하면 start/stop/delete가 잘못된 zone으로 호출돼 실패한다
        # (2026-09-11 실사용 테스트에서 발견: 생성된 VM을 인벤토리에서 삭제할 때 PROVIDER_API_ERROR).
        zone = outputs.get("zone")
        region = zone if zone else provider_spec.get("region")
        return instance_name, "Compute Engine Instance", region, instance_name
    if provider == "azure" and service_code == "sql_database":
        # 엔진마다 실제 Azure 서비스가 다르므로(MySQL/PostgreSQL Flexible Server, Azure SQL)
        # original_resource_type도 사람이 읽을 수 있게 구분한다(RDS Instance/Cloud SQL Instance와
        # 같은 관례 — 자세한 배경은 azure_database_provisioning.py 모듈 docstring 참고).
        server_name = outputs.get("server_name")
        engine_label = {
            "MySQL": "Azure Database for MySQL",
            "PostgreSQL": "Azure Database for PostgreSQL",
            "SQL Server": "Azure SQL Database",
        }.get(provider_spec.get("engine"), "Azure Database")
        return server_name, engine_label, provider_spec.get("region"), server_name
    if provider == "azure" and service_code == "storage_account":
        # app/providers/azure.py에는 아직 Storage Account discover/action 어댑터가 없다(CLAUDE.md
        # "resources/action SDK 어댑터 구현 범위" — 의도적으로 범위 밖). 계정 이름을 식별자로 쓴다
        # (S3/GCS 버킷과 같은 관례 — region은 azure/vm과 달리 계정 자체엔 zone 개념이 없어 그대로 사용).
        account_name = outputs.get("account_name")
        return account_name, "Microsoft.Storage/storageAccounts", provider_spec.get("region"), account_name
    if provider == "azure":
        # resource_actions.py는 Azure external_resource_id를 ARM 리소스 ID 전체로 가정한다.
        return outputs.get("vm_id"), "Microsoft.Compute/virtualMachines", provider_spec.get("region"), common_spec.get("name")
    return None, provider, provider_spec.get("region"), common_spec.get("name")


def _initial_resource_status(provider: str, service_code: str) -> str:
    """provisioning 직후(다음 동기화 전) resources 행에 채울 상태값.

    다음 실제 동기화가 `app/providers/{provider}.py`의 discover 결과로 곧 덮어쓰므로 정확한 실시간
    상태일 필요는 없지만, provider마다 상태 어휘가 달라(EC2 인스턴스 "RUNNING" vs S3 버킷은
    시작/중지 개념이 없어 "AVAILABLE") 서비스와 무관하게 "RUNNING"을 쓰면 어색하다.
    """
    if provider == "aws" and service_code == "s3":
        return "AVAILABLE"
    if provider == "aws" and service_code == "cloudfront":
        # aws_cloudfront_distribution은 기본값(wait_for_deployment=true)이라 apply가 배포
        # 완료(Deployed)까지 기다린 뒤 반환한다 — app/aws_cloudfront_provisioning.py 참고.
        return "DEPLOYED"
    if provider == "aws" and service_code == "rds":
        # aws_db_instance도 apply가 인스턴스 생성 완료(available)까지 기다린 뒤 반환한다.
        return "AVAILABLE"
    if provider == "gcp" and service_code == "cloud_cdn":
        # CloudFront와 동일 관례 — apply가 로드밸런서 스택 생성 완료까지 기다린 뒤 반환한다.
        return "DEPLOYED"
    if provider == "azure" and service_code == "storage_account":
        # S3 버킷과 같은 원칙 — 시작/중지 개념이 없는 리소스.
        return "AVAILABLE"
    if provider == "azure" and service_code == "sql_database":
        # RDS/Cloud SQL과 동일 — apply가 서버 생성 완료까지 기다린 뒤 반환한다.
        return "AVAILABLE"
    return "RUNNING"


def _create_resource_from_job(
    db: Session,
    job: ProvisioningJob,
    service: ServiceCatalog,
    account: CloudAccount,
    credential: Credential,
    outputs: dict,
    common_spec: dict,
    provider_spec: dict,
) -> str | None:
    """성공한 job에서 `resources` 행을 upsert한다 — 다음 동기화 없이도 INV-01에 바로 보이도록.
    생성된 리소스의 표시 이름을 반환한다(알림 문구용)."""
    external_id, resource_type, region, name = _resource_attrs(
        service.provider, service.service_code, outputs, common_spec, provider_spec
    )
    if not external_id:
        return name

    provider_resource_key = f"{service.provider}:{service.service_code}:{external_id}"
    now = dt.datetime.now(dt.timezone.utc)
    # 정가 기반 추정치(list_price_estimate) — 실제 CSP 비용 API를 호출하지 않는다(app/pricing.py
    # 참고). 사용량 기반 서비스(S3/CDN 등)나 허용 목록 밖 스펙은 None을 반환해 값을 지어내지 않는다.
    estimated_cost = estimate_monthly_cost_usd(service.provider, service.service_code, provider_spec)
    cost_source = "list_price_estimate" if estimated_cost is not None else None
    existing = (
        db.query(Resource)
        .filter_by(cloud_account_id=account.id, provider_resource_key=provider_resource_key)
        .one_or_none()
    )
    if existing is None:
        db.add(
            Resource(
                cloud_account_id=account.id,
                service_catalog_id=service.id,
                first_collected_by_credential_id=credential.id,
                last_collected_by_credential_id=credential.id,
                provider_resource_key=provider_resource_key,
                external_resource_id=external_id,
                original_resource_type=resource_type,
                name=name,
                region=region,
                status=_initial_resource_status(service.provider, service.service_code),
                estimated_monthly_cost=estimated_cost,
                cost_currency="USD" if estimated_cost is not None else None,
                cost_source=cost_source,
                cost_as_of=now if estimated_cost is not None else None,
                tags={"managed-by": "multi-cloud-platform", "job-id": str(job.id)},
                raw_metadata=outputs,
                first_seen_at=now,
                last_seen_at=now,
                last_synced_at=now,
            )
        )
    else:
        existing.last_collected_by_credential_id = credential.id
        existing.status = _initial_resource_status(service.provider, service.service_code)
        existing.estimated_monthly_cost = estimated_cost
        existing.cost_currency = "USD" if estimated_cost is not None else None
        existing.cost_source = cost_source
        existing.cost_as_of = now if estimated_cost is not None else None
        existing.last_seen_at = now
        existing.last_synced_at = now
        existing.is_stale = False
    job.created_resource_count = 1

    # GCP Cloud CDN이 버킷을 새로 만든 경우(app/gcp_cdn_provisioning.py 참고), 그 버킷이 인벤토리
    # 어디에도 안 보이면 사용자가 나중에 뭘 지워야 하는지 찾을 방법이 없다(2026-09-14 실사용 중
    # 지적받아 발견) — CDN 리소스 행과 별도로 "Cloud Storage Bucket" 행도 upsert한다. 실제 Storage
    # 프로비저닝으로 만든 버킷과 같은 (gcp, cloud_storage) service_catalog로 묶어야 인벤토리의
    # "서비스 종류" 필터에서도 Storage로 정상 분류된다 — 삭제 버튼은 다른 GCP Storage 버킷과
    # 동일하게 여전히 미지원(resource_actions.py에 (gcp, cloud_storage) 없음)이라, 이건 "보이게"만
    # 해결하는 것이지 "인벤토리에서 지울 수 있게"까지는 아니다.
    # 사용자가 "기존 버킷 사용"을 선택했다면(`backend_bucket_created=False`) 이 버킷은 우리가 만든
    # 게 아니므로 소유를 주장하는 행을 만들지 않는다 — 이미 그 계정 인벤토리에 있거나(동기화로
    # 보임) 우리가 관리하지 않는 외부 버킷이다. 출력에 이 키가 없으면(예: 구버전 테스트) 기존
    # 동작대로 True로 간주한다.
    if service.provider == "gcp" and service.service_code == "cloud_cdn":
        bucket_name = outputs.get("backend_bucket_name")
        bucket_created_by_us = outputs.get("backend_bucket_created", True)
        if bucket_name and bucket_created_by_us:
            storage_service = (
                db.query(ServiceCatalog).filter_by(provider="gcp", service_code="cloud_storage").one_or_none()
            )
            if storage_service is not None:
                bucket_key = f"gcp:cloud_storage:{bucket_name}"
                existing_bucket = (
                    db.query(Resource)
                    .filter_by(cloud_account_id=account.id, provider_resource_key=bucket_key)
                    .one_or_none()
                )
                if existing_bucket is None:
                    db.add(
                        Resource(
                            cloud_account_id=account.id,
                            service_catalog_id=storage_service.id,
                            first_collected_by_credential_id=credential.id,
                            last_collected_by_credential_id=credential.id,
                            provider_resource_key=bucket_key,
                            external_resource_id=bucket_name,
                            original_resource_type="Cloud Storage Bucket",
                            name=bucket_name,
                            # terraform/gcp/cloud_cdn/main.tf가 고정한 리전과 동일값(하드코딩) —
                            # google_storage_bucket.location을 그대로 읽으면 GCS가 대문자로
                            # 정규화해 돌려줘서(예: "ASIA-NORTHEAST3") 앱 전역의 소문자 관례와
                            # 어긋난다(Cloud Storage 러너가 이미 겪은 것과 같은 이유로 회피).
                            region="asia-northeast3",
                            status=_initial_resource_status("gcp", "cloud_storage"),
                            tags={"managed-by": "multi-cloud-platform", "job-id": str(job.id), "created-for": "cdn"},
                            raw_metadata={"forwarding_rule_name": outputs.get("forwarding_rule_name")},
                            first_seen_at=now,
                            last_seen_at=now,
                            last_synced_at=now,
                        )
                    )
                else:
                    existing_bucket.last_collected_by_credential_id = credential.id
                    existing_bucket.last_seen_at = now
                    existing_bucket.last_synced_at = now
                    existing_bucket.is_stale = False
                job.created_resource_count = 2

    db.flush()
    return name


def _create_notification(db: Session, job: ProvisioningJob, *, success: bool, resource_name: str | None) -> None:
    params: dict[str, Any] = {"resource": resource_name or job.workspace_name, "job_id": str(job.id)}
    if not success and job.error_message:
        params["reason"] = job.error_message
    db.add(
        Notification(
            user_id=job.user_id,
            type="provisioning_succeeded" if success else "provisioning_failed",
            reference_type="provisioning_job",
            reference_id=job.id,
            message_key="notif.provisioning.succeeded" if success else "notif.provisioning.failed",
            message_params=params,
        )
    )


def _finalize_job(db: Session, job: ProvisioningJob, service: ServiceCatalog | None, *, resource_name: str | None = None) -> None:
    """success/failed 종결 공통 마무리 — §14 "완료 시 알림 생성 + 감사 provisioning.complete 기록"을
    어느 실패 경로에서도 빠뜨리지 않는다. cancelled는 §14 기록 대상이 아니라 여기로 오지 않는다."""
    job.finished_at = dt.datetime.now(dt.timezone.utc)
    if job.status == "success":
        _create_notification(db, job, success=True, resource_name=resource_name)
    elif job.status == "failed":
        _create_notification(db, job, success=False, resource_name=resource_name)

    record_audit_event(
        db,
        actor_user_id=job.user_id,
        action="provisioning.complete",
        target_type="provisioning_job",
        target_id=str(job.id),
        result="success" if job.status == "success" else "failure",
        provider=service.provider if service else None,
        metadata={"workspace_name": job.workspace_name, "status": job.status, "error_code": job.error_code},
    )
    db.commit()


# --- 실행(백그라운드) -----------------------------------------------------------------------


def _execute_job(
    db: Session,
    job: ProvisioningJob,
    *,
    common_spec: dict | None = None,
    provider_spec: dict | None = None,
) -> None:
    """job 하나를 실제로 실행해 상태를 종결짓는다.

    `common_spec`/`provider_spec`이 주어지면 그것을(라우터가 메모리로 넘긴 원본 — Azure
    admin_password 포함) 러너에 전달하고, None이면 `job.spec_json`에서 읽는다(민감 필드 없는 러너
    또는 직접 호출 테스트용)."""
    if job.id in _CANCEL_REQUESTED:
        job.status = "cancelled"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        _CANCEL_REQUESTED.discard(job.id)
        db.commit()
        return

    stored = job.spec_json or {}
    cs = common_spec if common_spec is not None else stored.get("common_spec", {})
    ps = provider_spec if provider_spec is not None else stored.get("provider_spec", {})

    service = db.get(ServiceCatalog, job.service_catalog_id)
    credential = db.get(Credential, job.credential_id)
    account = db.get(CloudAccount, credential.cloud_account_id) if credential else None

    job.status = "running"
    job.started_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    # 자격 증명이 최소한 인증(STS 등)에 성공했는지만 확인한다.
    # 검증된 credential + provision 권한 확인 — resources.py의 resource_control 검사와 같은 정책:
    # permission_scope가 비어 있으면(아직 프로빙 안 된 목업 등) 건너뛴다.
    if credential is None or account is None or not credential.verified:
        job.status = "failed"
        job.error_code = "CLOUD_PERMISSION_DENIED"
        job.error_message = "검증된 자격 증명이 없습니다."
        _finalize_job(db, job, service)
        return

    # provision 권한은 여기서 사전 차단하지 않는다 — `permission_scope.provision`은 AWS의 경우
    # `iam:SimulatePrincipalPolicy`로 프로빙하는데, EC2 권한만 있는 키(예: AmazonEC2FullAccess)는
    # IAM 시뮬레이션 권한이 없어 실제로는 생성 가능한데도 provision=false로 잘못 기록된다(false
    runner = get_runner(service.provider, service.service_code)
    if runner is None:
        # 요청 시점에 501로 걸렀어야 하지만 방어적으로 한 번 더 막는다.
        job.status = "failed"
        job.error_code = "PROVISIONING_NOT_IMPLEMENTED"
        job.error_message = "지원하지 않는 프로비저닝 조합입니다."
        _finalize_job(db, job, service)
        return

    try:
        secret_payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
    except CredentialEncryptionError:
        job.status = "failed"
        job.error_code = "PROVIDER_API_ERROR"
        job.error_message = "자격 증명을 복호화하지 못했습니다."
        _finalize_job(db, job, service)
        return

    workspace_dir = Path(get_settings().terraform_workspaces_dir) / job.workspace_name
    try:
        result = runner.run(
            job_id=job.id,
            workspace_dir=workspace_dir,
            project_id=account.external_account_id,
            common_spec=cs,
            provider_spec=ps,
            secret_payload=secret_payload,
            cancel_check=lambda: job.id in _CANCEL_REQUESTED,
        )
    finally:
        del secret_payload

    resource_name: str | None = None
    if result.cancelled or job.id in _CANCEL_REQUESTED:
        job.status = "cancelled"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        _CANCEL_REQUESTED.discard(job.id)
        db.commit()
        return
    if result.success:
        job.status = "success"
        job.progress_percent = 100
        job.terraform_state_ref = str(workspace_dir)
        job.result_json = result.outputs or {}
        resource_name = _create_resource_from_job(
            db, job, service, account, credential, result.outputs or {}, cs, ps
        )
    else:
        job.status = "failed"
        job.error_code = result.error_code
        job.error_message = result.error_message

    _finalize_job(db, job, service, resource_name=resource_name)


def _run_provisioning_job(job_id: int, common_spec: dict | None = None, provider_spec: dict | None = None) -> None:
    """백그라운드 진입점. `SessionLocal()`은 테스트 트랜잭션과 무관한 별도 커넥션이라 HTTP 계층
    테스트에서는 이 함수 자체를 monkeypatch로 no-op화한다."""
    db = SessionLocal()
    try:
        job = db.get(ProvisioningJob, job_id)
        if job is None:
            return
        _execute_job(db, job, common_spec=common_spec, provider_spec=provider_spec)
    finally:
        db.close()


# --- 엔드포인트 ----------------------------------------------------------------------------


@router.post("/provisioning/{provider}/{service}", response_model=ProvisioningJobCreateResponse, status_code=202)
def create_provisioning_job(
    provider: str,
    service: str,
    payload: CreateProvisioningJobRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _confirmed: None = Depends(require_confirmation),
) -> ProvisioningJobCreateResponse:
    if not idempotency_key:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key 헤더가 필요합니다.")

    service_catalog = db.query(ServiceCatalog).filter_by(provider=provider, service_code=service).one_or_none()
    if service_catalog is None:
        raise ApiError(404, "SERVICE_NOT_FOUND", "서비스를 찾을 수 없습니다.")
    if not service_catalog.provisionable:
        raise ApiError(422, "RESOURCE_NOT_PROVISIONABLE", "이 서비스는 프로비저닝할 수 없습니다.")

    runner = get_runner(provider, service)
    if runner is None:
        raise ApiError(501, "PROVISIONING_NOT_IMPLEMENTED", "이 provider/service 조합은 아직 지원하지 않습니다.")

    sensitive = getattr(runner, "SENSITIVE_PROVIDER_SPEC_FIELDS", frozenset())
    offending = _find_secret_field(payload.common_spec, "common_spec", frozenset()) or _find_secret_field(
        payload.provider_spec, "provider_spec", sensitive
    )
    if offending:
        raise ApiError(
            422, "SECRET_FIELD_NOT_ALLOWED", "spec에 자격 증명으로 의심되는 필드가 포함되어 있습니다.",
            details=[{"field": offending, "reason": "secret_like_field"}],
        )

    credential, cloud_account = _get_owned_credential(db, current_user.id, payload.credential_id)
    if cloud_account.provider != provider:
        # 소유 credential이지만 다른 provider — 존재를 드러내지 않고 404로 처리한다.
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")

    runner.validate_spec(payload.common_spec, payload.provider_spec)

    # DB엔 민감 필드(admin_password 등)를 제거한 provider_spec을 저장하고, 실행엔 원본을 메모리로 넘긴다.
    stored_provider_spec = _strip_sensitive(payload.provider_spec, sensitive)
    spec_json = {"common_spec": payload.common_spec, "provider_spec": stored_provider_spec}

    existing = (
        db.query(ProvisioningJob)
        .filter_by(user_id=current_user.id, idempotency_key=idempotency_key)
        .one_or_none()
    )
    if existing is not None:
        same_payload = (
            existing.spec_json == spec_json
            and existing.credential_id == credential.id
            and existing.service_catalog_id == service_catalog.id
        )
        if not same_payload:
            raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 Idempotency-Key로 다른 내용의 요청이 이미 존재합니다.")
        return ProvisioningJobCreateResponse(
            data=ProvisioningJobCreateData(
                id=str_id(existing.id),
                status=existing.status,
                created_at=iso_z(existing.created_at),
                status_url=f"/api/v1/provisioning/jobs/{existing.id}",
            )
        )

    job = ProvisioningJob(
        user_id=current_user.id,
        credential_id=credential.id,
        service_catalog_id=service_catalog.id,
        workspace_name=f"pending-{idempotency_key}",
        idempotency_key=idempotency_key,
        spec_json=spec_json,
        status="queued",
    )
    db.add(job)
    db.flush()
    job.workspace_name = f"user-{current_user.id}-job-{job.id}"
    db.flush()

    record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="provisioning.request",
        target_type="provisioning_job",
        target_id=str(job.id),
        result="requested",
        provider=provider,
        metadata={"workspace_name": job.workspace_name},
        request_id=_request_id(request),
    )
    db.commit()
    db.refresh(job)

    background_tasks.add_task(_run_provisioning_job, job.id, payload.common_spec, payload.provider_spec)

    return ProvisioningJobCreateResponse(
        data=ProvisioningJobCreateData(
            id=str_id(job.id),
            status=job.status,
            created_at=iso_z(job.created_at),
            status_url=f"/api/v1/provisioning/jobs/{job.id}",
        )
    )


@router.get("/provisioning/jobs", response_model=ProvisioningJobListResponse)
def list_provisioning_jobs(
    provider: str | None = None,
    service_catalog_id: str | None = None,
    status: str | None = None,
    credential_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProvisioningJobListResponse:
    query = db.query(ProvisioningJob).filter(ProvisioningJob.user_id == current_user.id)
    if status:
        query = query.filter(ProvisioningJob.status == status)
    if credential_id:
        query = query.filter(
            ProvisioningJob.credential_id == _parse_id(credential_id, "CREDENTIAL_NOT_FOUND", "자격 증명을 찾을 수 없습니다.")
        )
    if service_catalog_id:
        query = query.filter(
            ProvisioningJob.service_catalog_id
            == _parse_id(service_catalog_id, "SERVICE_NOT_FOUND", "서비스를 찾을 수 없습니다.")
        )
    if provider:
        query = query.join(ServiceCatalog, ServiceCatalog.id == ProvisioningJob.service_catalog_id).filter(
            ServiceCatalog.provider == provider
        )

    jobs = query.order_by(ProvisioningJob.id.desc()).all()
    items = [_serialize_job(j) for j in jobs]
    return ProvisioningJobListResponse(data=ProvisioningJobListData(items=items, total=len(items)))


@router.get("/provisioning/jobs/{job_id}", response_model=ProvisioningJobResponse)
def get_provisioning_job(
    job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProvisioningJobResponse:
    job = _get_owned_job(db, current_user.id, job_id)
    return ProvisioningJobResponse(data=_serialize_job(job))


@router.post("/provisioning/jobs/{job_id}/cancel", response_model=ProvisioningJobResponse, status_code=202)
def cancel_provisioning_job(
    job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProvisioningJobResponse:
    job = _get_owned_job(db, current_user.id, job_id)
    if job.status not in ("queued", "running"):
        raise ApiError(409, "JOB_NOT_CANCELLABLE", "이미 종료된 작업은 취소할 수 없습니다.")

    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    else:
        _CANCEL_REQUESTED.add(job.id)

    db.refresh(job)
    return ProvisioningJobResponse(data=_serialize_job(job))
