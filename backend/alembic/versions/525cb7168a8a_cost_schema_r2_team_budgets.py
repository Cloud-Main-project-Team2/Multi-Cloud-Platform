"""cost schema R2 — team_budgets + team_budget_notifications

반복 예산의 한도를 바꿀 때 기존 행을 수정하지 않고 새 행을 추가해 과거 기간의 예산 대비
수치가 소급 변경되지 않게 한다(team_budgets). 임계(80/100%) 알림의 최초 1회 발송은
team_budget_notifications의 UNIQUE(team_budget_id, period_start, threshold)가 보장한다
(docs/DB_ERD_v1.2.md Part B R2).

Revision ID: 525cb7168a8a
Revises: fa0ba565197b
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "525cb7168a8a"
down_revision: Union[str, None] = "fa0ba565197b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_budgets",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("period_type", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("limit_amount", sa.Numeric(precision=19, scale=6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="USD", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
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
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_team_budgets_team_id"), "team_budgets", ["team_id"], unique=False)
    op.create_index(
        "ix_team_budgets_team_period", "team_budgets", ["team_id", "period_type", "start_date"], unique=False
    )

    op.create_table(
        "team_budget_notifications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("team_budget_id", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("threshold", sa.SmallInteger(), nullable=False),
        sa.Column("notification_id", sa.BigInteger(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("threshold IN (80, 100)", name="ck_team_budget_notifications_threshold"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_budget_id"], ["team_budgets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "team_budget_id", "period_start", "threshold", name="uq_team_budget_notifications_key"
        ),
    )
    op.create_index(
        op.f("ix_team_budget_notifications_team_budget_id"),
        "team_budget_notifications", ["team_budget_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_team_budget_notifications_team_budget_id"), table_name="team_budget_notifications"
    )
    op.drop_table("team_budget_notifications")

    op.drop_index("ix_team_budgets_team_period", table_name="team_budgets")
    op.drop_index(op.f("ix_team_budgets_team_id"), table_name="team_budgets")
    op.drop_table("team_budgets")
