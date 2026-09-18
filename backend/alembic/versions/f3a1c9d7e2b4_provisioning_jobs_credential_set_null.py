"""provisioning_jobs.credential_id를 nullable + ON DELETE SET NULL로 전환

credential 삭제(`DELETE /credentials/{id}`)는 진행 중인 job이 없으면 허용하도록 앱 코드가
짜여 있는데, 이 컬럼이 `NOT NULL` + `ON DELETE RESTRICT`라 한 번이라도 프로비저닝에 쓰인
credential은 (성공/실패와 무관하게) 절대 삭제가 안 되고 500(IntegrityError)이 났다
(2026-09-18, 실사용자 리포트로 발견). `resource_sync_job_items.credential_id`는 이미
`ON DELETE SET NULL`로 이 문제가 없어 같은 패턴을 따른다 — job 자체(스펙·결과·상태)는 남기고
참조만 끊는다. UI 안내문구("삭제해도 이미 수집된 리소스 기록은 남습니다")와도 이게 맞다.

Revision ID: f3a1c9d7e2b4
Revises: a1b2c3d4e5f6
Create Date: 2026-09-18
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f3a1c9d7e2b4"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("provisioning_jobs", "credential_id", nullable=True)
    op.drop_constraint("provisioning_jobs_credential_id_fkey", "provisioning_jobs", type_="foreignkey")
    op.create_foreign_key(
        "provisioning_jobs_credential_id_fkey",
        "provisioning_jobs",
        "credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("provisioning_jobs_credential_id_fkey", "provisioning_jobs", type_="foreignkey")
    op.create_foreign_key(
        "provisioning_jobs_credential_id_fkey",
        "provisioning_jobs",
        "credentials",
        ["credential_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("provisioning_jobs", "credential_id", nullable=False)
