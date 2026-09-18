"""merge cost schema and credential nullable heads

비용 관리 MVP(#100)와 provisioning_jobs.credential_id nullable 전환(#98)이 같은 부모
(a1b2c3d4e5f6)에서 각자 갈라져 나가 alembic head가 2개가 됐다(2026-09-18, `alembic upgrade
head`가 "Multiple head revisions"로 실패하는 걸 발견 — main을 새로 받은 사람은 전부 서버가
못 뜬다). 두 체인 모두 스키마 변경 자체는 서로 겹치지 않으니, 병합만 하고 별도 작업은 없다.

Revision ID: 592d650854f0
Revises: 24378fc90e8d, f3a1c9d7e2b4
Create Date: 2026-09-18
"""

from typing import Sequence, Union

revision: str = "592d650854f0"
down_revision: Union[str, tuple[str, ...], None] = ("24378fc90e8d", "f3a1c9d7e2b4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
