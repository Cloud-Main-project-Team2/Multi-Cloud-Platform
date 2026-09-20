"""team_budgets — 반복 예산 (team_id, start_date) 부분 UNIQUE (R2 보강)

Revision ID: b7c2d9e4f1a3
Revises: 592d650854f0
Create Date: 2026-09-20

PR 7의 "활성 반복 예산 중복 → 409" 검사는 애플리케이션이 SELECT 뒤 INSERT로 하므로 같은 시작일의
POST가 동시에 오면 둘 다 통과한다(2026-09-20 프론트 스모크에서 실제 재현). 같은 팀·같은 시작일의
반복 행(end_date IS NULL)을 DB가 막는다. custom(end_date NOT NULL)은 겹침 검사가 별개라 대상이 아니다.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b7c2d9e4f1a3"
down_revision: Union[str, tuple[str, ...], None] = "592d650854f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "uq_team_budgets_recurring_start"


def upgrade() -> None:
    op.create_index(
        INDEX, "team_budgets", ["team_id", "start_date"], unique=True,
        postgresql_where="end_date IS NULL",
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="team_budgets")
