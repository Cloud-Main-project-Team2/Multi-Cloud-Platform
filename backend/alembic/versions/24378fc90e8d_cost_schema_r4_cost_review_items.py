"""cost schema R4 — cost_review_items

급증 탐지·검토 큐 공용 테이블. 전용 급증 테이블을 따로 만들지 않는다 —
uq_cost_review_items_user_source가 "(계정·서비스·날짜) 최초 1회 알림"의 중복 방지를 겸한다
(docs/DB_ERD_v1.2.md Part B R4).

Revision ID: 24378fc90e8d
Revises: 2440e52749bc
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "24378fc90e8d"
down_revision: Union[str, None] = "2440e52749bc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cost_review_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("resolution", sa.String(length=30), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'resolved' AND resolution IS NOT NULL AND resolved_at IS NOT NULL) "
            "OR (status <> 'resolved' AND resolved_at IS NULL)",
            name="ck_cost_review_items_resolved_consistency",
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('too_small','expected','unexpected')",
            name="ck_cost_review_items_resolution",
        ),
        sa.CheckConstraint("status IN ('open','investigating','resolved')", name="ck_cost_review_items_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "source_type", "source_key", name="uq_cost_review_items_user_source"),
    )
    op.create_index("ix_cost_review_items_status", "cost_review_items", ["status"], unique=False)
    op.create_index(op.f("ix_cost_review_items_user_id"), "cost_review_items", ["user_id"], unique=False)
    op.create_index(
        "ix_cost_review_items_user_status", "cost_review_items", ["user_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_cost_review_items_user_status", table_name="cost_review_items")
    op.drop_index(op.f("ix_cost_review_items_user_id"), table_name="cost_review_items")
    op.drop_index("ix_cost_review_items_status", table_name="cost_review_items")
    op.drop_table("cost_review_items")
