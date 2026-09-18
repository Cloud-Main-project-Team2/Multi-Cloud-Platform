"""resources.status_changed_at — 비용 확장(PR 1) 상태 변경 시각 기록 시작

최적화 추천(유휴/장기정지 판정)은 "상태가 언제 바뀌었는지"가 필요한데 이 시각은 소급 계산이
불가능하다. 지금부터 기록을 시작하지 않으면 나중에 만들 방법이 없다(docs/DB_ERD_v1.2.md Part B
R0). 기존 행은 전부 NULL — "판정 불가"로 다루며 0일로 취급하지 않는다.

Revision ID: dde461ba65fd
Revises: a1b2c3d4e5f6
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "dde461ba65fd"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("resources", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_resources_status_changed_at", "resources", ["status_changed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_resources_status_changed_at", table_name="resources")
    op.drop_column("resources", "status_changed_at")
