"""report_generations.cost_snapshot — 보고서 "비용 요약" 생성 시점 값 고정(2026-09-19)

사용률/미사용 리소스와 달리 비용은 "다시 열어도 같은 숫자"가 요구사항이라(비용 파트
권형님_보고서_비용연동_개발프롬프트 §9), 매번 재조회하지 않고 생성 시점에 계산한 결과를
그대로 저장한다. app/report_cost.py::build_cost_snapshot() 참고.

Revision ID: c4d8a2f6b1e9
Revises: b3e6f1a9c7d2
Create Date: 2026-09-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d8a2f6b1e9"
down_revision: Union[str, None] = "b3e6f1a9c7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("report_generations", sa.Column("cost_snapshot", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("report_generations", "cost_snapshot")
