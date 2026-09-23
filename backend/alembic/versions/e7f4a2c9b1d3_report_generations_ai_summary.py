"""report_generations.ai_summary — 보고서 "AI 분석 요약" 생성 시점 값 고정(2026-09-23)

`cost_snapshot`과 같은 이유로 생성 시점에 계산한 결과를 그대로 저장한다 —
app/report_summary.py::build_ai_summary() 참고. 지금까지 이 섹션은 프론트(reports-data.js)의
고정 목업 문구였다(사용자 확인, 2026-09-23).

Revision ID: e7f4a2c9b1d3
Revises: c4d8a2f6b1e9
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7f4a2c9b1d3"
down_revision: Union[str, None] = "c4d8a2f6b1e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("report_generations", sa.Column("ai_summary", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("report_generations", "ai_summary")
