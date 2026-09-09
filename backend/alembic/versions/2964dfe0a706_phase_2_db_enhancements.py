"""phase 2 db enhancements

Adds provisioning_requests (parent of provisioning_jobs), resource_types
(normalizes CSP original resource types), cloud_resource_costs (cost
history, separate from resources' latest-cost summary columns), and
audit_events (structured audit log for security/destructive actions).

This migration assumes existing data may already be present in
provisioning_jobs and resources, so new required relationships are added
nullable first, backfilled, validated, then tightened to NOT NULL where
the backfill is guaranteed complete (provisioning_jobs.provisioning_request_id).
resources.resource_type_id stays nullable indefinitely — collection code
does not populate it reliably yet.

Revision ID: 2964dfe0a706
Revises: 7bf7892874c3
Create Date: 2026-09-09 14:44:55.667682

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2964dfe0a706'
down_revision: Union[str, None] = '7bf7892874c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 12개 provisionable 서비스 각각에 대한 최소 프로비저닝 타입 1개.
# type_code/display_name은 frontend/inventory.html 확정 목업(EC2 Instance, Virtual Machine,
# Compute Engine, SQL Database)과 각 CSP 공식 용어(AWS: bucket/distribution/DB Instance,
# Azure: storage account/endpoint, GCP: instance/bucket)를 근거로 한다. 세부 사양(인스턴스
# 패밀리 등)은 추측하지 않았고, gcp/cloud_cdn만 자체 리소스 개념이 없어 형제 CDN 서비스
# (CloudFront distribution, Azure CDN endpoint)와 일관되게 "distribution"으로 명명했다 —
# 이 항목은 실제 CSP 용어 확인 후 조정될 수 있음을 README에 남긴다.
RESOURCE_TYPE_SEED = [
    ("aws", "ec2", "instance", "EC2 Instance"),
    ("aws", "rds", "db_instance", "RDS DB Instance"),
    ("aws", "s3", "bucket", "S3 Bucket"),
    ("aws", "cloudfront", "distribution", "CloudFront Distribution"),
    ("azure", "vm", "virtual_machine", "Virtual Machine"),
    ("azure", "sql_database", "database", "SQL Database"),
    ("azure", "storage_account", "storage_account", "Storage Account"),
    ("azure", "cdn", "endpoint", "CDN Endpoint"),
    ("gcp", "compute_engine", "instance", "Compute Engine Instance"),
    ("gcp", "cloud_sql", "instance", "Cloud SQL Instance"),
    ("gcp", "cloud_storage", "bucket", "Cloud Storage Bucket"),
    ("gcp", "cloud_cdn", "distribution", "Cloud CDN Distribution"),
]


def upgrade() -> None:
    # ---- 1) resource_types 테이블 생성 ----
    op.create_table('resource_types',
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
    sa.UniqueConstraint('service_catalog_id', 'type_code', name='uq_resource_types_service_catalog_type_code')
    )
    op.create_index(op.f('ix_resource_types_service_catalog_id'), 'resource_types', ['service_catalog_id'], unique=False)

    # ---- 2) resource_types 최소 시드 (12개 provisionable 서비스 각 1개, provisionable=true) ----
    # 이후 단계에서 반드시 필요한 다른 테이블(provisioning_requests, resources.resource_type_id
    # backfill)이 이 데이터에 의존하므로 테이블 구조 생성 직후, 다른 무엇보다 먼저 시드한다.
    values_sql = ", ".join(
        f"('{provider}', '{service_code}', '{type_code}', '{display_name}')"
        for provider, service_code, type_code, display_name in RESOURCE_TYPE_SEED
    )
    op.execute(
        f"""
        INSERT INTO resource_types (service_catalog_id, type_code, display_name, category, provisionable)
        SELECT sc.id, v.type_code, v.display_name, sc.category, true
        FROM (VALUES {values_sql}) AS v(provider, service_code, type_code, display_name)
        JOIN service_catalog sc ON sc.provider = v.provider AND sc.service_code = v.service_code
        ON CONFLICT (service_catalog_id, type_code) DO NOTHING
        """
    )

    # ---- 3) provisioning_requests 테이블 생성 (resource_types 시드 완료 후) ----
    op.create_table('provisioning_requests',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('request_key', sa.String(length=255), nullable=False),
    sa.Column('resource_type_id', sa.BigInteger(), nullable=False),
    sa.Column('common_spec_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=30), server_default='queued', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('queued','running','success','partial_success','failed','cancelled')", name='ck_provisioning_requests_status'),
    sa.CheckConstraint('finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at', name='ck_provisioning_requests_finished_after_started'),
    sa.ForeignKeyConstraint(['resource_type_id'], ['resource_types.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'request_key', name='uq_provisioning_requests_user_request_key')
    )
    op.create_index(op.f('ix_provisioning_requests_resource_type_id'), 'provisioning_requests', ['resource_type_id'], unique=False)
    op.create_index(op.f('ix_provisioning_requests_user_id'), 'provisioning_requests', ['user_id'], unique=False)

    # ---- 4) cloud_resource_costs 테이블 생성 ----
    op.create_table('cloud_resource_costs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('resource_id', sa.BigInteger(), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('cost_kind', sa.String(length=30), nullable=False),
    sa.Column('amount', sa.Numeric(precision=19, scale=6), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.Column('source_record_key', sa.String(length=512), nullable=False),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("cost_kind IN ('actual','estimated','list_price_estimate')", name='ck_cloud_resource_costs_cost_kind'),
    sa.CheckConstraint("provider IN ('aws','azure','gcp')", name='ck_cloud_resource_costs_provider'),
    sa.CheckConstraint('amount >= 0', name='ck_cloud_resource_costs_amount_non_negative'),
    sa.CheckConstraint('period_end >= period_start', name='ck_cloud_resource_costs_period_end_after_start'),
    sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'source_record_key', name='uq_cloud_resource_costs_provider_source_record_key')
    )
    op.create_index('ix_cloud_resource_costs_as_of', 'cloud_resource_costs', ['as_of'], unique=False)
    op.create_index('ix_cloud_resource_costs_provider_kind_period', 'cloud_resource_costs', ['provider', 'cost_kind', 'period_start'], unique=False)
    op.create_index(op.f('ix_cloud_resource_costs_resource_id'), 'cloud_resource_costs', ['resource_id'], unique=False)
    op.create_index('ix_cloud_resource_costs_resource_period', 'cloud_resource_costs', ['resource_id', 'period_start', 'period_end'], unique=False)

    # ---- 5) audit_events 테이블 생성 ----
    op.create_table('audit_events',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('actor_user_id', sa.BigInteger(), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('target_type', sa.String(length=100), nullable=False),
    sa.Column('target_id', sa.String(length=255), nullable=True),
    sa.Column('request_id', sa.String(length=100), nullable=True),
    sa.Column('result', sa.String(length=30), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=True),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("provider IS NULL OR provider IN ('aws','azure','gcp')", name='ck_audit_events_provider'),
    sa.CheckConstraint("result IN ('requested','success','failure','denied')", name='ck_audit_events_result'),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_events_action_created', 'audit_events', ['action', 'created_at'], unique=False)
    op.create_index('ix_audit_events_actor_created', 'audit_events', ['actor_user_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_audit_events_actor_user_id'), 'audit_events', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_audit_events_request_id'), 'audit_events', ['request_id'], unique=False)
    op.create_index('ix_audit_events_target', 'audit_events', ['target_type', 'target_id', 'created_at'], unique=False)

    # ---- 6) provisioning_jobs: 새 컬럼 추가. provisioning_request_id는 우선 nullable로 ----
    op.add_column('provisioning_jobs', sa.Column('provisioning_request_id', sa.BigInteger(), nullable=True))
    op.add_column('provisioning_jobs', sa.Column('progress_percent', sa.Integer(), server_default='0', nullable=False))
    op.add_column('provisioning_jobs', sa.Column('terraform_state_ref', sa.String(length=1024), nullable=True))
    op.add_column('provisioning_jobs', sa.Column('created_resource_count', sa.Integer(), server_default='0', nullable=False))
    op.create_index(op.f('ix_provisioning_jobs_provisioning_request_id'), 'provisioning_jobs', ['provisioning_request_id'], unique=False)
    op.create_check_constraint(
        'ck_provisioning_jobs_progress_percent_range',
        'provisioning_jobs',
        'progress_percent >= 0 AND progress_percent <= 100',
    )
    op.create_check_constraint(
        'ck_provisioning_jobs_created_resource_count_non_negative',
        'provisioning_jobs',
        'created_resource_count >= 0',
    )

    # ---- 7) backfill: 기존 provisioning_jobs 각 행에 합성 provisioning_requests 부모를 생성 ----
    # request_key는 'legacy-job-<job.id>'로 합성한다(job.id가 전역적으로 유일하므로
    # UNIQUE(user_id, request_key)와 충돌하지 않는다). resource_type_id는 job의
    # service_catalog_id로 방금 시드한 resource_types를 조인해 결정한다(1서비스 = 1타입이라
    # 결과가 항상 유일하다).
    op.execute(
        """
        WITH backfilled AS (
            INSERT INTO provisioning_requests
                (user_id, request_key, resource_type_id, common_spec_json, status, created_at, started_at, finished_at)
            SELECT
                pj.user_id,
                'legacy-job-' || pj.id,
                rt.id,
                pj.spec_json,
                pj.status,
                pj.created_at,
                pj.started_at,
                pj.finished_at
            FROM provisioning_jobs pj
            JOIN resource_types rt ON rt.service_catalog_id = pj.service_catalog_id
            WHERE pj.provisioning_request_id IS NULL
            RETURNING id AS request_id, request_key
        )
        UPDATE provisioning_jobs pj
        SET provisioning_request_id = b.request_id
        FROM backfilled b
        WHERE b.request_key = 'legacy-job-' || pj.id
        """
    )

    # ---- 8) validate: backfill 후 NULL이 남아있으면(= service_catalog_id에 매핑된 타입이 없는
    # 예외적인 기존 job이 있으면) 안전하게 실패시키고, 있으면 NOT NULL로 강화 ----
    connection = op.get_bind()
    remaining_null = connection.execute(
        sa.text("SELECT count(*) FROM provisioning_jobs WHERE provisioning_request_id IS NULL")
    ).scalar_one()
    if remaining_null:
        raise RuntimeError(
            f"{remaining_null}개의 provisioning_jobs 행을 provisioning_requests로 backfill하지 "
            "못했습니다(서비스에 매핑된 resource_type이 없음). NOT NULL로 전환하기 전에 원인을 "
            "확인하세요."
        )
    op.create_foreign_key(
        'fk_provisioning_jobs_provisioning_request_id',
        'provisioning_jobs', 'provisioning_requests', ['provisioning_request_id'], ['id'], ondelete='CASCADE',
    )
    op.alter_column('provisioning_jobs', 'provisioning_request_id', nullable=False)

    # ---- 9) resources: resource_type_id 컬럼 추가(계속 nullable) ----
    op.add_column('resources', sa.Column('resource_type_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_resources_resource_type_id'), 'resources', ['resource_type_id'], unique=False)
    op.create_foreign_key(
        'fk_resources_resource_type_id_resource_types',
        'resources', 'resource_types', ['resource_type_id'], ['id'], ondelete='RESTRICT',
    )

    # ---- 10) resources.resource_type_id backfill: service_catalog_id에 정확히 하나의
    # resource_type만 대응되는 경우에만 채운다. 애매한 매핑은 만들지 않고 NULL로 남긴다. ----
    op.execute(
        """
        UPDATE resources r
        SET resource_type_id = rt.id
        FROM resource_types rt
        WHERE rt.service_catalog_id = r.service_catalog_id
          AND r.resource_type_id IS NULL
          AND (
                SELECT count(*) FROM resource_types rt2
                WHERE rt2.service_catalog_id = r.service_catalog_id
              ) = 1
        """
    )


def downgrade() -> None:
    # 경고: 아래 downgrade는 되돌릴 수 없는 데이터 손실을 일으킨다.
    #   - provisioning_requests 전체(합성 backfill 부모 포함)와 그 안의 common_spec_json
    #   - provisioning_jobs의 progress_percent/terraform_state_ref/created_resource_count 값
    #   - resources.resource_type_id에 채워진 backfill 결과
    #   - cloud_resource_costs, audit_events, resource_types 전체
    # 운영 데이터가 있는 환경에서는 실행 전 반드시 백업하거나 이 downgrade를 사용하지 않는다.
    print(
        "WARNING: downgrading 2964dfe0a706 drops provisioning_requests, resource_types, "
        "cloud_resource_costs, audit_events and their data, and clears the new "
        "provisioning_jobs/resources columns this migration backfilled. This is not reversible."
    )

    op.drop_constraint('fk_resources_resource_type_id_resource_types', 'resources', type_='foreignkey')
    op.drop_index(op.f('ix_resources_resource_type_id'), table_name='resources')
    op.drop_column('resources', 'resource_type_id')

    op.drop_constraint('fk_provisioning_jobs_provisioning_request_id', 'provisioning_jobs', type_='foreignkey')
    op.drop_constraint('ck_provisioning_jobs_created_resource_count_non_negative', 'provisioning_jobs', type_='check')
    op.drop_constraint('ck_provisioning_jobs_progress_percent_range', 'provisioning_jobs', type_='check')
    op.drop_index(op.f('ix_provisioning_jobs_provisioning_request_id'), table_name='provisioning_jobs')
    op.drop_column('provisioning_jobs', 'created_resource_count')
    op.drop_column('provisioning_jobs', 'terraform_state_ref')
    op.drop_column('provisioning_jobs', 'progress_percent')
    op.drop_column('provisioning_jobs', 'provisioning_request_id')

    op.drop_index('ix_cloud_resource_costs_resource_period', table_name='cloud_resource_costs')
    op.drop_index(op.f('ix_cloud_resource_costs_resource_id'), table_name='cloud_resource_costs')
    op.drop_index('ix_cloud_resource_costs_provider_kind_period', table_name='cloud_resource_costs')
    op.drop_index('ix_cloud_resource_costs_as_of', table_name='cloud_resource_costs')
    op.drop_table('cloud_resource_costs')

    op.drop_index(op.f('ix_provisioning_requests_user_id'), table_name='provisioning_requests')
    op.drop_index(op.f('ix_provisioning_requests_resource_type_id'), table_name='provisioning_requests')
    op.drop_table('provisioning_requests')

    op.drop_index(op.f('ix_resource_types_service_catalog_id'), table_name='resource_types')
    op.drop_table('resource_types')

    op.drop_index('ix_audit_events_target', table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_request_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_actor_user_id'), table_name='audit_events')
    op.drop_index('ix_audit_events_actor_created', table_name='audit_events')
    op.drop_index('ix_audit_events_action_created', table_name='audit_events')
    op.drop_table('audit_events')
