"""cost schema R1 — teams + cloud_accounts.team_id

한 사용자 안의 클라우드 계정 묶음. 예산 한도는 여기 두지 않는다(R2의 team_budgets가 이력을
보존한다). cloud_accounts.team_id는 nullable + ON DELETE SET NULL이다 — 팀을 지워도 계정과
비용 데이터는 남는다(docs/DB_ERD_v1.2.md Part B R1, docs/비용_개발문서/06_DB변경.md).

Revision ID: fa0ba565197b
Revises: dde461ba65fd
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fa0ba565197b"
down_revision: Union[str, None] = "dde461ba65fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="USD", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_teams_user_name"),
    )
    op.create_index(op.f("ix_teams_user_id"), "teams", ["user_id"], unique=False)

    op.add_column("cloud_accounts", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_cloud_accounts_team_id"), "cloud_accounts", ["team_id"], unique=False)
    op.create_foreign_key(
        "fk_cloud_accounts_team_id_teams",
        "cloud_accounts", "teams", ["team_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_cloud_accounts_team_id_teams", "cloud_accounts", type_="foreignkey")
    op.drop_index(op.f("ix_cloud_accounts_team_id"), table_name="cloud_accounts")
    op.drop_column("cloud_accounts", "team_id")

    op.drop_index(op.f("ix_teams_user_id"), table_name="teams")
    op.drop_table("teams")
