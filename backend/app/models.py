from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(CreatedAtMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    affiliation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    affiliation_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active", default="active")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        sa.CheckConstraint("affiliation_type IN ('company','individual')", name="ck_users_affiliation_type"),
        sa.CheckConstraint("status IN ('active','withdrawn')", name="ck_users_status"),
    )


class SocialAccount(CreatedAtMixin, Base):
    __tablename__ = "social_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_social_accounts_provider_provider_user_id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_social_accounts_user_id_provider"),
    )


class PasswordResetToken(CreatedAtMixin, Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailVerification(CreatedAtMixin, Base):
    """회원가입 전 이메일 검증(OTP). 계정이 아직 없으므로 user_id가 아니라 email 기준으로 키를 잡는다.
    같은 이메일로 재전송하면 기존 미검증 행을 갱신(overwrite)한다."""

    __tablename__ = "email_verifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa.text("0"), default=0)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RefreshToken(CreatedAtMixin, Base):
    """회전(rotation)하는 opaque refresh token. 발급 시 DB에 해시로 저장하고, /auth/refresh에서
    기존 토큰을 폐기(revoked_at)하며 새 토큰을 발급한다. 로그아웃 시에도 폐기한다."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CloudAccount(CreatedAtMixin, Base):
    __tablename__ = "cloud_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 비용 확장(PR 3) — 팀은 "한 사용자 안의 계정 묶음"이다. 팀을 지워도 계정·비용 데이터는
    # 남아야 하므로 SET NULL. cloud_account_costs에는 team_id를 두지 않는다 — 팀 재배정이
    # 과거 비용까지 소급해 움직이면 안 되기 때문이다(docs/DB_ERD_v1.2.md Part B R1).
    team_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.CheckConstraint("provider IN ('aws','azure','gcp')", name="ck_cloud_accounts_provider"),
        sa.UniqueConstraint(
            "user_id", "provider", "external_account_id", name="uq_cloud_accounts_user_provider_external"
        ),
    )


class Credential(CreatedAtMixin, Base):
    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cloud_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(50), nullable=False)
    public_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permission_scope: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"), default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.CheckConstraint("display_order >= 0", name="ck_credentials_display_order_non_negative"),
        sa.UniqueConstraint("cloud_account_id", "name", name="uq_credentials_cloud_account_name"),
    )


class ServiceCatalog(CreatedAtMixin, Base):
    __tablename__ = "service_catalog"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    service_code: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provisionable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false"), default=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.CheckConstraint("provider IN ('aws','azure','gcp')", name="ck_service_catalog_provider"),
        sa.UniqueConstraint("provider", "service_code", name="uq_service_catalog_provider_service_code"),
    )


class ProvisioningJob(CreatedAtMixin, Base):
    """한 credential, 한 provider, 한 Terraform workspace에 대한 실제 실행 단위.

    이전 API 명세(provider별 개별 엔드포인트) 유지 결정에 따라 상위 부모
    ProvisioningRequest 개념은 제거됐다. status는 이 테이블의 row 단위로 관리한다.
    """

    __tablename__ = "provisioning_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # credential이 삭제되면 이 job이 참조하던 자격 증명은 사라지지만 job 자체(스펙·결과·상태)는
    # 감사 기록으로 남아야 한다(2026-09-18) — resource_sync_job_items.credential_id와 동일 원칙.
    credential_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True, index=True
    )
    service_catalog_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service_catalog.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workspace_name: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="queued", default="queued")

    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    # Terraform state 본문·output은 DB에 저장하지 않는다. 안전한 외부 저장소(S3/GCS backend 등)의
    # 참조(키·경로)만 저장한다.
    terraform_state_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_resource_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('queued','running','success','failed','cancelled')", name="ck_provisioning_jobs_status"
        ),
        sa.UniqueConstraint("user_id", "workspace_name", name="uq_provisioning_jobs_user_workspace"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_provisioning_jobs_user_idempotency"),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_provisioning_jobs_finished_after_started",
        ),
        sa.CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100", name="ck_provisioning_jobs_progress_percent_range"
        ),
        sa.CheckConstraint(
            "created_resource_count >= 0", name="ck_provisioning_jobs_created_resource_count_non_negative"
        ),
    )


class Notification(CreatedAtMixin, Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_key: Mapped[str] = mapped_column(String(255), nullable=False)
    message_params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"), default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Resource(CreatedAtMixin, Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cloud_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_catalog_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service_catalog.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    first_collected_by_credential_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_collected_by_credential_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_resource_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    external_resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    original_resource_type: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 상태가 실제로 바뀐 시각(같은 값으로 덮어쓴 동기화는 갱신하지 않는다). 소급 계산이 불가능해
    # 지금부터 기록을 시작한다 — 기존 행은 전부 NULL("판정 불가", 0일로 취급하지 않는다).
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_monthly_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6), nullable=True)
    collected_cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 6), nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(sa.CHAR(3), nullable=True)
    cost_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    cost_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    cost_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict)
    raw_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"), default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.CheckConstraint(
            "estimated_monthly_cost IS NULL OR estimated_monthly_cost >= 0",
            name="ck_resources_estimated_cost_non_negative",
        ),
        sa.CheckConstraint(
            "collected_cost_amount IS NULL OR collected_cost_amount >= 0",
            name="ck_resources_collected_cost_non_negative",
        ),
        sa.UniqueConstraint(
            "cloud_account_id", "provider_resource_key", name="uq_resources_cloud_account_provider_key"
        ),
        sa.Index("ix_resources_cloud_account_service", "cloud_account_id", "service_catalog_id"),
        sa.Index("ix_resources_cloud_account_status", "cloud_account_id", "status"),
        sa.Index("ix_resources_region", "region"),
        sa.Index("ix_resources_last_synced_at", "last_synced_at"),
        sa.Index("ix_resources_status_changed_at", "status_changed_at"),
        sa.Index("ix_resources_tags_gin", "tags", postgresql_using="gin"),
    )

class CloudResourceCost(CreatedAtMixin, Base):
    """리소스 비용 이력. resources의 cost_* 컬럼(최신 요약)과는 별개다.

    resources.estimated_monthly_cost/collected_cost_amount는 화면 빠른 조회용 "최신 스냅샷"이고,
    이 테이블은 기간별 비용 레코드의 전체 이력이다. actual/estimated/list_price_estimate를
    같은 의미로 합산하지 않는다.
    """

    __tablename__ = "cloud_resource_costs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resource_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    cost_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    currency: Mapped[str] = mapped_column(sa.CHAR(3), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    # 같은 provider의 원본 비용 레코드를 재수집해도 중복 삽입되지 않도록 하는 멱등 키.
    source_record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        sa.CheckConstraint("provider IN ('aws','azure','gcp')", name="ck_cloud_resource_costs_provider"),
        sa.CheckConstraint(
            "cost_kind IN ('actual','estimated','list_price_estimate')", name="ck_cloud_resource_costs_cost_kind"
        ),
        sa.CheckConstraint("amount >= 0", name="ck_cloud_resource_costs_amount_non_negative"),
        sa.CheckConstraint("period_end >= period_start", name="ck_cloud_resource_costs_period_end_after_start"),
        sa.UniqueConstraint(
            "provider", "source_record_key", name="uq_cloud_resource_costs_provider_source_record_key"
        ),
        sa.Index("ix_cloud_resource_costs_resource_period", "resource_id", "period_start", "period_end"),
        sa.Index("ix_cloud_resource_costs_provider_kind_period", "provider", "cost_kind", "period_start"),
        sa.Index("ix_cloud_resource_costs_as_of", "as_of"),
    )

class ResourceSyncJob(CreatedAtMixin, Base):
    __tablename__ = "resource_sync_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending", default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending','running','success','partial_success','failed','cancelled')",
            name="ck_resource_sync_jobs_status",
        ),
    )


class ResourceSyncJobItem(CreatedAtMixin, Base):
    __tablename__ = "resource_sync_job_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sync_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("resource_sync_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cloud_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cloud_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    credential_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending", default="pending")
    resources_discovered: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    resources_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    resources_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    resources_marked_stale: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        sa.CheckConstraint("provider IN ('aws','azure','gcp')", name="ck_resource_sync_job_items_provider"),
        sa.CheckConstraint(
            "status IN ('pending','running','success','failed','cancelled')",
            name="ck_resource_sync_job_items_status",
        ),
        sa.CheckConstraint("resources_discovered >= 0", name="ck_rsji_discovered_non_negative"),
        sa.CheckConstraint("resources_created >= 0", name="ck_rsji_created_non_negative"),
        sa.CheckConstraint("resources_updated >= 0", name="ck_rsji_updated_non_negative"),
        sa.CheckConstraint("resources_marked_stale >= 0", name="ck_rsji_marked_stale_non_negative"),
        sa.UniqueConstraint("sync_job_id", "cloud_account_id", name="uq_rsji_sync_job_cloud_account"),
    )

class AuditEvent(CreatedAtMixin, Base):
    """감사 가능한 보안·파괴적 작업 이벤트. 일반 애플리케이션 로그와 별개다.

    metadata_json에는 절대 다음을 넣지 않는다: 비밀번호·JWT, 클라우드 access/secret key나
    service account JSON, 복호화된 credential payload, Terraform secret variable·민감 output,
    비밀번호 재설정 원본 토큰. 이 테이블에 대한 수정·삭제 API는 만들지 않는다.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict
    )

    __table_args__ = (
        sa.CheckConstraint(
            "result IN ('requested','success','failure','denied')", name="ck_audit_events_result"
        ),
        sa.CheckConstraint(
            "provider IS NULL OR provider IN ('aws','azure','gcp')", name="ck_audit_events_provider"
        ),
        sa.Index("ix_audit_events_actor_created", "actor_user_id", "created_at"),
        sa.Index("ix_audit_events_target", "target_type", "target_id", "created_at"),
        sa.Index("ix_audit_events_action_created", "action", "created_at"),
    )


# ── 비용 확장(PR 3) — docs/DB_ERD_v1.2.md Part B, docs/비용_개발문서/06_DB변경.md가 정본이다.
# 기존 cloud_resource_costs(리소스 단위·양수 전용)는 그대로 두고, 계정 단위·음수 허용 실측
# 비용은 아래 CloudAccountCost가 대신한다. 리비전은 R1~R4 4개로 나눈다.


class Team(CreatedAtMixin, Base):
    """한 사용자 안의 클라우드 계정 묶음(R1). 팀원 공유(조직 RBAC)는 범위 밖이다. 예산 한도는
    여기 두지 않는다 — TeamBudget이 이력을 보존한다(한도를 바꿀 때 기존 행을 수정하지 않고
    새 행을 추가한다)."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    currency: Mapped[str] = mapped_column(sa.CHAR(3), nullable=False, server_default="USD", default="USD")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (sa.UniqueConstraint("user_id", "name", name="uq_teams_user_name"),)


class TeamBudget(CreatedAtMixin, Base):
    """예산 이력(R2). 반복 예산의 한도를 바꿀 때 기존 행을 수정하지 않고 새 행을 추가해 과거
    기간의 예산 대비 수치가 소급 변경되지 않게 한다. end_date가 NULL인 행이 반복 예산이고,
    같은 팀의 다음 행 start_date가 사실상의 종료다. custom만 end_date를 가진다(제외 경계로
    저장)."""

    __tablename__ = "team_budgets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    currency: Mapped[str] = mapped_column(sa.CHAR(3), nullable=False, server_default="USD", default="USD")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.CheckConstraint(
            "period_type IN ('monthly','quarterly','annual','custom')", name="ck_team_budgets_period_type"
        ),
        sa.CheckConstraint("limit_amount > 0", name="ck_team_budgets_limit_positive"),
        sa.CheckConstraint(
            "(period_type = 'custom' AND end_date IS NOT NULL) "
            "OR (period_type <> 'custom' AND end_date IS NULL)",
            name="ck_team_budgets_custom_end_required",
        ),
        sa.CheckConstraint("end_date IS NULL OR end_date > start_date", name="ck_team_budgets_end_after_start"),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date <= start_date + INTERVAL '1 year'",
            name="ck_team_budgets_custom_max_one_year",
        ),
        sa.Index("ix_team_budgets_team_period", "team_id", "period_type", "start_date"),
        # 반복 예산은 팀당 시작일이 유일하다 — 애플리케이션의 "활성 반복 중복 409" 검사가 동시 요청에
        # 뚫리는 것을 DB가 막는다(2026-09-20, b7c2d9e4f1a3). custom은 겹침 검사가 따로 있어 제외.
        sa.Index(
            "uq_team_budgets_recurring_start", "team_id", "start_date", unique=True,
            postgresql_where=sa.text("end_date IS NULL"),
        ),
    )


class TeamBudgetNotification(CreatedAtMixin, Base):
    """예산 임계(80/100%) 알림의 최초 1회 발송을 보장하는 중복 방지 기록(R2). notifications
    행과 같은 트랜잭션에 저장한다 — INSERT가 성공했을 때만 알림을 만든다(PR 7,
    app/cost/budget_alert.py)."""

    __tablename__ = "team_budget_notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_budget_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("team_budgets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    threshold: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    notification_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True
    )
    notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        sa.CheckConstraint("threshold IN (80, 100)", name="ck_team_budget_notifications_threshold"),
        sa.UniqueConstraint(
            "team_budget_id", "period_start", "threshold", name="uq_team_budget_notifications_key"
        ),
    )


class CloudAccountCost(CreatedAtMixin, Base):
    """계정 단위 실측 비용(R3, PR 4 수집). cloud_resource_costs(기존, 리소스 단위·양수 전용)와
    별개다 — 리소스에 귀속되지 않는 금액(데이터 전송료·지원 요금)과 크레딧·환불(음수)을 담기
    위해 새로 만들었다. 저장 단위는 계정×서비스×charge_category×일. 재수집은 UPSERT가 아니라
    (cloud_account_id, source, period_start 범위)로 지운 뒤 다시 넣는다(PR 4, app/cost/ingest.py).
    team_id는 두지 않는다 — 팀 재배정이 과거 비용까지 소급해 움직이면 안 되기 때문이다."""

    __tablename__ = "cloud_account_costs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cloud_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("resources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    cost_kind: Mapped[str] = mapped_column(String(30), nullable=False, server_default="actual", default="actual")
    charge_category: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="usage", default="usage"
    )
    service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)  # 음수 허용(크레딧·환불) — CHECK 없음
    currency: Mapped[str] = mapped_column(sa.CHAR(3), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)  # 포함
    period_end: Mapped[date] = mapped_column(Date, nullable=False)  # 제외
    is_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false"), default=False
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), default=dict)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        sa.CheckConstraint("provider IN ('aws','azure','gcp')", name="ck_cloud_account_costs_provider"),
        sa.CheckConstraint("cost_kind IN ('actual')", name="ck_cloud_account_costs_cost_kind"),
        sa.CheckConstraint(
            "charge_category IN ('usage','credit','refund','tax','other')",
            name="ck_cloud_account_costs_charge_category",
        ),
        sa.CheckConstraint("period_end > period_start", name="ck_cloud_account_costs_period_end_after_start"),
        sa.UniqueConstraint(
            "provider", "source_record_key", name="uq_cloud_account_costs_provider_source_record_key"
        ),
        sa.Index("ix_cloud_account_costs_account_period", "cloud_account_id", "period_start", "period_end"),
        sa.Index("ix_cloud_account_costs_provider_period", "provider", "period_start"),
        sa.Index("ix_cloud_account_costs_service_period", "cloud_account_id", "service", "period_start"),
        sa.Index("ix_cloud_account_costs_as_of", "as_of"),
        sa.Index("ix_cloud_account_costs_tags_gin", "tags", postgresql_using="gin"),
    )


class CostIngestionRun(CreatedAtMixin, Base):
    """계정당 run 1행(R3) — resource_sync_jobs(부모)+resource_sync_job_items(자식) 2단 구조와
    다르다. "계정당 1시간 1회" 제한과 capability 판정(그 계정 마지막 행의 status/error_code)을
    인덱스 하나로 끝내기 위해서다. 동시 실행 방지는 이 테이블이 아니라 PostgreSQL advisory
    lock으로 한다 — pg_try_advisory_lock(hashtext('cost_ingest'), cloud_account_id)."""

    __tablename__ = "cost_ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cloud_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending", default="pending")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    api_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    records_replaced: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        sa.CheckConstraint("trigger_type IN ('auto','manual')", name="ck_cost_ingestion_runs_trigger_type"),
        sa.CheckConstraint(
            "status IN ('pending','running','success','partial_success','failed','cancelled')",
            name="ck_cost_ingestion_runs_status",
        ),
        sa.CheckConstraint("api_calls >= 0", name="ck_cost_ingestion_runs_api_calls_non_negative"),
        sa.CheckConstraint("period_end > period_start", name="ck_cost_ingestion_runs_period_end_after_start"),
        sa.Index("ix_cost_ingestion_runs_account_requested", "cloud_account_id", "requested_at"),
        sa.Index("ix_cost_ingestion_runs_status", "status"),
    )


class CostReviewItem(CreatedAtMixin, Base):
    """급증 탐지·검토 큐 공용(R4). 전용 급증 테이블을 만들지 않는다 —
    uq_cost_review_items_user_source가 "(계정·서비스·날짜) 최초 1회 알림"의 중복 방지를 겸한다.
    UNIQUE에 user_id를 포함한 이유 — 전역 UNIQUE면 다른 사용자의 항목이 서로를 막아 급증
    알림이 조용히 사라질 수 있다."""

    __tablename__ = "cost_review_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open", default="open")
    resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.CheckConstraint("status IN ('open','investigating','resolved')", name="ck_cost_review_items_status"),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('too_small','expected','unexpected')",
            name="ck_cost_review_items_resolution",
        ),
        sa.CheckConstraint(
            "(status = 'resolved' AND resolution IS NOT NULL AND resolved_at IS NOT NULL) "
            "OR (status <> 'resolved' AND resolved_at IS NULL)",
            name="ck_cost_review_items_resolved_consistency",
        ),
        sa.UniqueConstraint("user_id", "source_type", "source_key", name="uq_cost_review_items_user_source"),
        sa.Index("ix_cost_review_items_status", "status"),
        sa.Index("ix_cost_review_items_user_status", "user_id", "status"),
    )


class ReportDeliverySetting(CreatedAtMixin, Base):
    """보고서 작성(reports.html) "정기 발송" 설정(2026-09-19) — 지금까지는 이 값이 브라우저
    localStorage에만 있어서 백엔드 스케줄러가 "누구에게 언제 보낼지" 알 방법이 없었다. 사용자당
    1행(UNIQUE user_id) — 웹 다운로드는 즉시 실행이라 저장할 설정이 없고, 이 표는 사실상
    "메일 정기 발송을 켠 사용자" 목록이다. `last_sent_at`으로 같은 주기 안에서 중복 발송을
    막는다(app/report_scheduler.py 참고)."""

    __tablename__ = "report_delivery_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    delivery_method: Mapped[str] = mapped_column(String(10), nullable=False, server_default="WEB", default="WEB")
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="WEEKLY", default="WEEKLY")
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        sa.CheckConstraint("delivery_method IN ('WEB','EMAIL')", name="ck_report_delivery_settings_method"),
        sa.CheckConstraint(
            "period_type IN ('DAILY','WEEKLY','MONTHLY','HALF_YEARLY')", name="ck_report_delivery_settings_period"
        ),
        sa.CheckConstraint(
            "delivery_method <> 'EMAIL' OR email IS NOT NULL", name="ck_report_delivery_settings_email_required"
        ),
    )
