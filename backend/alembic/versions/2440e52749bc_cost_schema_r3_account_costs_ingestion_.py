"""cost schema R3 — cloud_account_costs + cost_ingestion_runs

계정 단위 실측 비용(cloud_account_costs) — 기존 cloud_resource_costs(리소스 단위·양수 전용)와
별개다. resource_id는 nullable(리소스에 귀속되지 않는 계정 단위 금액을 담기 위해), amount에는
CHECK를 걸지 않는다(크레딧·환불이 음수). cost_ingestion_runs는 계정당 run 1행이다 —
resource_sync_jobs(부모)+resource_sync_job_items(자식) 2단 구조와 다르다(docs/DB_ERD_v1.2.md
Part B R3).

Revision ID: 2440e52749bc
Revises: 525cb7168a8a
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2440e52749bc"
down_revision: Union[str, None] = "525cb7168a8a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cloud_account_costs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("cloud_account_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("cost_kind", sa.String(length=30), server_default="actual", nullable=False),
        sa.Column("charge_category", sa.String(length=20), server_default="usage", nullable=False),
        sa.Column("service", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("is_estimated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("source_record_key", sa.String(length=512), nullable=False),
        sa.Column(
            "tags", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("provider IN ('aws','azure','gcp')", name="ck_cloud_account_costs_provider"),
        sa.CheckConstraint("cost_kind IN ('actual')", name="ck_cloud_account_costs_cost_kind"),
        sa.CheckConstraint(
            "charge_category IN ('usage','credit','refund','tax','other')",
            name="ck_cloud_account_costs_charge_category",
        ),
        sa.CheckConstraint("period_end > period_start", name="ck_cloud_account_costs_period_end_after_start"),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "source_record_key", name="uq_cloud_account_costs_provider_source_record_key"
        ),
    )
    op.create_index(
        "ix_cloud_account_costs_account_period",
        "cloud_account_costs", ["cloud_account_id", "period_start", "period_end"], unique=False,
    )
    op.create_index("ix_cloud_account_costs_as_of", "cloud_account_costs", ["as_of"], unique=False)
    op.create_index(
        op.f("ix_cloud_account_costs_cloud_account_id"), "cloud_account_costs", ["cloud_account_id"], unique=False
    )
    op.create_index(
        "ix_cloud_account_costs_provider_period", "cloud_account_costs", ["provider", "period_start"], unique=False
    )
    op.create_index(
        op.f("ix_cloud_account_costs_resource_id"), "cloud_account_costs", ["resource_id"], unique=False
    )
    op.create_index(
        "ix_cloud_account_costs_service_period",
        "cloud_account_costs", ["cloud_account_id", "service", "period_start"], unique=False,
    )
    op.create_index(
        "ix_cloud_account_costs_tags_gin", "cloud_account_costs", ["tags"], unique=False, postgresql_using="gin"
    )

    op.create_table(
        "cost_ingestion_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("cloud_account_id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("api_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_replaced", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','running','success','partial_success','failed','cancelled')",
            name="ck_cost_ingestion_runs_status",
        ),
        sa.CheckConstraint("trigger_type IN ('auto','manual')", name="ck_cost_ingestion_runs_trigger_type"),
        sa.CheckConstraint("api_calls >= 0", name="ck_cost_ingestion_runs_api_calls_non_negative"),
        sa.CheckConstraint("period_end > period_start", name="ck_cost_ingestion_runs_period_end_after_start"),
        sa.ForeignKeyConstraint(["cloud_account_id"], ["cloud_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cost_ingestion_runs_account_requested",
        "cost_ingestion_runs", ["cloud_account_id", "requested_at"], unique=False,
    )
    op.create_index(
        op.f("ix_cost_ingestion_runs_cloud_account_id"), "cost_ingestion_runs", ["cloud_account_id"], unique=False
    )
    op.create_index("ix_cost_ingestion_runs_status", "cost_ingestion_runs", ["status"], unique=False)
    op.create_index(op.f("ix_cost_ingestion_runs_user_id"), "cost_ingestion_runs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cost_ingestion_runs_user_id"), table_name="cost_ingestion_runs")
    op.drop_index("ix_cost_ingestion_runs_status", table_name="cost_ingestion_runs")
    op.drop_index(op.f("ix_cost_ingestion_runs_cloud_account_id"), table_name="cost_ingestion_runs")
    op.drop_index("ix_cost_ingestion_runs_account_requested", table_name="cost_ingestion_runs")
    op.drop_table("cost_ingestion_runs")

    op.drop_index("ix_cloud_account_costs_tags_gin", table_name="cloud_account_costs", postgresql_using="gin")
    op.drop_index("ix_cloud_account_costs_service_period", table_name="cloud_account_costs")
    op.drop_index(op.f("ix_cloud_account_costs_resource_id"), table_name="cloud_account_costs")
    op.drop_index("ix_cloud_account_costs_provider_period", table_name="cloud_account_costs")
    op.drop_index(op.f("ix_cloud_account_costs_cloud_account_id"), table_name="cloud_account_costs")
    op.drop_index("ix_cloud_account_costs_as_of", table_name="cloud_account_costs")
    op.drop_index("ix_cloud_account_costs_account_period", table_name="cloud_account_costs")
    op.drop_table("cloud_account_costs")
