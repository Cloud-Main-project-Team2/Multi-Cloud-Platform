"""report delivery settings — 보고서 정기 메일 발송 설정

지금까지 "정기 발송" 설정(웹 다운로드/메일 전송, 수신 주소, 주기)은 브라우저 localStorage에만
있어서 백엔드 스케줄러가 "누구에게 언제 보낼지" 알 방법이 없었다(2026-09-19). 사용자당 1행
(UNIQUE user_id) — 웹 다운로드는 즉시 실행이라 저장할 설정이 없어서, 이 표는 사실상 "메일
정기 발송을 켠 사용자" 목록이다. `last_sent_at`으로 같은 주기 안에서 중복 발송을 막는다
(app/report_scheduler.py 참고).

Revision ID: 6f1a2d9e4c3b
Revises: 592d650854f0
Create Date: 2026-09-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6f1a2d9e4c3b"
down_revision: Union[str, None] = "592d650854f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_delivery_settings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("delivery_method", sa.String(length=10), server_default="WEB", nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("period_type", sa.String(length=20), server_default="WEEKLY", nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_report_delivery_settings_user_id"),
        sa.CheckConstraint("delivery_method IN ('WEB','EMAIL')", name="ck_report_delivery_settings_method"),
        sa.CheckConstraint(
            "period_type IN ('DAILY','WEEKLY','MONTHLY','HALF_YEARLY')", name="ck_report_delivery_settings_period"
        ),
        sa.CheckConstraint(
            "delivery_method <> 'EMAIL' OR email IS NOT NULL", name="ck_report_delivery_settings_email_required"
        ),
    )
    op.create_index(
        op.f("ix_report_delivery_settings_user_id"), "report_delivery_settings", ["user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_report_delivery_settings_user_id"), table_name="report_delivery_settings")
    op.drop_table("report_delivery_settings")
