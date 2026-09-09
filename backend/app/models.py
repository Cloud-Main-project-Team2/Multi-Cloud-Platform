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


class CloudAccount(CreatedAtMixin, Base):
    __tablename__ = "cloud_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
    __tablename__ = "provisioning_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    credential_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("credentials.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service_catalog_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service_catalog.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workspace_name: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="queued", default="queued")
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
        sa.Index("ix_resources_tags_gin", "tags", postgresql_using="gin"),
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
