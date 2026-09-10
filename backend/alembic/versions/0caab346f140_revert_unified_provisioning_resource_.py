"""revert unified provisioning/resource-type api support

이전 API 명세(provider별 개별 엔드포인트) 유지 결정에 따라, 신규 통합 API를
위해서만 추가됐던 스키마 조각을 되돌린다. 되돌리는 대상은 딱 4가지다:

  1) provisioning_jobs.provisioning_request_id 컬럼(FK/인덱스 포함)
  2) provisioning_requests 테이블
  3) resources.resource_type_id 컬럼(FK/인덱스 포함)
  4) resource_types 테이블

2964dfe0a706에서 함께 추가된 나머지(cloud_resource_costs, audit_events,
provisioning_jobs.progress_percent/terraform_state_ref/created_resource_count,
credentials 암호화·인벤토리 캐시 구조 등)는 확정된 개선이므로 건드리지 않는다.

운영 데이터가 아직 없는 상태를 전제로 하며, 2964dfe0a706 파일 자체는 고치지
않고 그 위에 이 revision을 새로 쌓아 히스토리를 다시 쓰지 않는다.

Revision ID: 0caab346f140
Revises: 2964dfe0a706
Create Date: 2026-09-10 01:40:12.278483

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0caab346f140'
down_revision: Union[str, None] = '2964dfe0a706'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 자식 → 부모 순서로 제거해야 FK 제약에 걸리지 않는다.

    # 1) provisioning_jobs.provisioning_request_id (FK/인덱스/컬럼) 제거
    op.drop_constraint(
        'fk_provisioning_jobs_provisioning_request_id',
        'provisioning_jobs',
        type_='foreignkey',
    )
    op.drop_index(
        op.f('ix_provisioning_jobs_provisioning_request_id'),
        table_name='provisioning_jobs',
    )
    op.drop_column('provisioning_jobs', 'provisioning_request_id')

    # 2) provisioning_requests 테이블 제거 (resource_types를 참조하므로 먼저 제거)
    op.drop_index(op.f('ix_provisioning_requests_user_id'), table_name='provisioning_requests')
    op.drop_index(op.f('ix_provisioning_requests_resource_type_id'), table_name='provisioning_requests')
    op.drop_table('provisioning_requests')

    # 3) resources.resource_type_id (FK/인덱스/컬럼) 제거
    op.drop_constraint(
        'fk_resources_resource_type_id_resource_types',
        'resources',
        type_='foreignkey',
    )
    op.drop_index(op.f('ix_resources_resource_type_id'), table_name='resources')
    op.drop_column('resources', 'resource_type_id')

    # 4) resource_types 테이블 제거
    op.drop_index(op.f('ix_resource_types_service_catalog_id'), table_name='resource_types')
    op.drop_table('resource_types')


def downgrade() -> None:
    # 부모 → 자식 순서로 재생성. 2964dfe0a706의 원본 정의와 동일한 구조/이름으로
    # 되돌린다(운영 데이터가 없다는 전제하의 왕복 테스트용이며, 제거됐던 데이터는
    # 복구되지 않는다).

    # 4) resource_types 테이블 재생성
    op.create_table(
        'resource_types',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('service_catalog_id', sa.BigInteger(), nullable=False),
        sa.Column('type_code', sa.String(length=150), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=False),
        sa.Column('provisionable', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('supports_start', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('supports_stop', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('supports_delete', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['service_catalog_id'], ['service_catalog.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('service_catalog_id', 'type_code', name='uq_resource_types_service_catalog_type_code'),
    )
    op.create_index(op.f('ix_resource_types_service_catalog_id'), 'resource_types', ['service_catalog_id'], unique=False)

    # 3) resources.resource_type_id (컬럼/인덱스/FK) 재생성 — 원본과 동일하게 nullable
    op.add_column('resources', sa.Column('resource_type_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_resources_resource_type_id'), 'resources', ['resource_type_id'], unique=False)
    op.create_foreign_key(
        'fk_resources_resource_type_id_resource_types',
        'resources', 'resource_types', ['resource_type_id'], ['id'], ondelete='RESTRICT',
    )

    # 2) provisioning_requests 테이블 재생성
    op.create_table(
        'provisioning_requests',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('request_key', sa.String(length=255), nullable=False),
        sa.Column('resource_type_id', sa.BigInteger(), nullable=False),
        sa.Column('common_spec_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.String(length=30), server_default='queued', nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','success','partial_success','failed','cancelled')",
            name='ck_provisioning_requests_status',
        ),
        sa.CheckConstraint(
            'finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at',
            name='ck_provisioning_requests_finished_after_started',
        ),
        sa.ForeignKeyConstraint(['resource_type_id'], ['resource_types.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'request_key', name='uq_provisioning_requests_user_request_key'),
    )
    op.create_index(op.f('ix_provisioning_requests_resource_type_id'), 'provisioning_requests', ['resource_type_id'], unique=False)
    op.create_index(op.f('ix_provisioning_requests_user_id'), 'provisioning_requests', ['user_id'], unique=False)

    # 1) provisioning_jobs.provisioning_request_id (컬럼/인덱스/FK) 재생성
    op.add_column(
        'provisioning_jobs',
        sa.Column('provisioning_request_id', sa.BigInteger(), nullable=False),
    )
    op.create_index(
        op.f('ix_provisioning_jobs_provisioning_request_id'),
        'provisioning_jobs',
        ['provisioning_request_id'],
        unique=False,
    )
    op.create_foreign_key(
        'fk_provisioning_jobs_provisioning_request_id',
        'provisioning_jobs', 'provisioning_requests',
        ['provisioning_request_id'], ['id'], ondelete='CASCADE',
    )
