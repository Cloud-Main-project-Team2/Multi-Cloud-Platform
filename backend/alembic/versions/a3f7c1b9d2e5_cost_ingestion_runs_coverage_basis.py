"""cost_ingestion_runs.coverage_basis — 수집 결과의 "확인 근거"를 run마다 기록(2026-09-23, A-2)

왜 컬럼이 필요한가: 어느 날짜를 "확인됨"으로 볼 수 있는지는 **정책**이고, 그 정책은 시간이 지나며
바뀐다(새 CSP가 검증되면 승격). 코드 상수로만 두면 승격이 **과거 데이터의 해석까지 소급해서**
바꿔 버린다 — 예전에 미확인이던 날이 어느 날 갑자기 확인됨이 된다. 그래서 그 run이 만들어진
시점의 근거를 행에 남긴다(app/cost/coverage.py).

값: 'complete_range'(요청 범위를 확인 근거로 인정 — 기존 AWS 정책) | 'observed_only'(받은 금액만
관측). NULL = 이 컬럼 추가 이전 run이며, **provider가 aws일 때만** complete_range로 읽는다.
기존 데이터 백필은 하지 않는다 — 백필하면 그 시점에 없던 근거를 만들어 넣는 셈이다.

Revision ID: a3f7c1b9d2e5
Revises: e7f4a2c9b1d3
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c1b9d2e5"
down_revision: Union[str, None] = "e7f4a2c9b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cost_ingestion_runs",
        sa.Column("coverage_basis", sa.String(length=20), nullable=True),
    )
    # 허용 값 검증 — NULL은 레거시라 허용한다(백필하지 않기 때문).
    op.create_check_constraint(
        "ck_cost_ingestion_runs_coverage_basis",
        "cost_ingestion_runs",
        "coverage_basis IS NULL OR coverage_basis IN ('complete_range','observed_only')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_cost_ingestion_runs_coverage_basis", "cost_ingestion_runs", type_="check")
    op.drop_column("cost_ingestion_runs", "coverage_basis")
