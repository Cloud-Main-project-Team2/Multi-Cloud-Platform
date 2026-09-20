"""report generations — 보고서 "생성 이력" 실저장(2026-09-19) + main 병합 지점

지금까지 "생성 이력"은 브라우저 localStorage에만 있어서 팀원끼리 공유가 안 되고, 같은 조건으로
다시 생성할 때마다 새 행이 계속 쌓여 지저분해지는 문제가 있었다(사용자 실사용 중 확인 — 같은
주간 보고서가 여러 번 중복 표시됨). `(user_id, period_type, period_from, period_to, providers)`
UNIQUE로 "같은 조건"을 정의하고, 라우터가 `ON CONFLICT ... DO UPDATE`로 `generated_at`만
갱신한다 — app/models.py의 ReportGeneration 참고.

**두 갈래를 여기서 합친다(2026-09-20)**: `6f1a2d9e4c3b`(이 브랜치)와 `b7c2d9e4f1a3`
(main, PR 8 후속 #113 — team_budgets 반복 예산 UNIQUE)가 같은 부모(`592d650854f0`)에서 갈라져
있었다. main을 나중에 실제로 병합하기 전에 미리 여기서 두 head를 합쳐 둔다 — 병합 시점에 따로
머지 마이그레이션을 만들 필요가 없게 하기 위함(둘 다 이미 커밋된 적 없는 로컬 작업이라 지금
합쳐도 안전하다).

Revision ID: b3e6f1a9c7d2
Revises: 6f1a2d9e4c3b, b7c2d9e4f1a3
Create Date: 2026-09-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3e6f1a9c7d2"
down_revision: Union[str, tuple[str, ...], None] = ("6f1a2d9e4c3b", "b7c2d9e4f1a3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_generations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("period_type", sa.String(length=20), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("providers", sa.String(length=20), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "period_type", "period_from", "period_to", "providers",
            name="uq_report_generations_params",
        ),
        sa.CheckConstraint(
            "period_type IN ('DAILY','WEEKLY','MONTHLY','HALF_YEARLY')", name="ck_report_generations_period"
        ),
    )
    op.create_index(op.f("ix_report_generations_user_id"), "report_generations", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_report_generations_user_id"), table_name="report_generations")
    op.drop_table("report_generations")
