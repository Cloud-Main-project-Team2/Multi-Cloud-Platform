import sqlalchemy as sa

EXPECTED_TABLES = {
    "users",
    "social_accounts",
    "password_reset_tokens",
    "cloud_accounts",
    "credentials",
    "service_catalog",
    "provisioning_jobs",
    "notifications",
    "resources",
    "resource_sync_jobs",
    "resource_sync_job_items",
    "cloud_resource_costs",
    "audit_events",
}


def test_all_tables_exist(engine):
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert EXPECTED_TABLES.issubset(tables)


def test_core_unique_constraints_exist(engine):
    inspector = sa.inspect(engine)

    users_uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("users")}
    assert ("normalized_email",) in users_uniques

    credential_uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("credentials")}
    assert ("cloud_account_id", "name") in credential_uniques

    catalog_uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("service_catalog")}
    assert ("provider", "service_code") in catalog_uniques

    resource_uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("resources")}
    assert ("cloud_account_id", "provider_resource_key") in resource_uniques

    job_uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("provisioning_jobs")}
    assert ("user_id", "workspace_name") in job_uniques
    assert ("user_id", "idempotency_key") in job_uniques


def test_core_check_constraints_exist(engine):
    inspector = sa.inspect(engine)

    users_checks = {c["name"] for c in inspector.get_check_constraints("users")}
    assert "ck_users_status" in users_checks

    cloud_account_checks = {c["name"] for c in inspector.get_check_constraints("cloud_accounts")}
    assert "ck_cloud_accounts_provider" in cloud_account_checks

    job_checks = {c["name"] for c in inspector.get_check_constraints("provisioning_jobs")}
    assert "ck_provisioning_jobs_finished_after_started" in job_checks


def test_core_foreign_keys_exist(engine):
    inspector = sa.inspect(engine)

    cloud_account_fks = {fk["referred_table"] for fk in inspector.get_foreign_keys("cloud_accounts")}
    assert "users" in cloud_account_fks

    credential_fks = {fk["referred_table"] for fk in inspector.get_foreign_keys("credentials")}
    assert "cloud_accounts" in credential_fks

    resource_fks = {fk["referred_table"] for fk in inspector.get_foreign_keys("resources")}
    assert "cloud_accounts" in resource_fks
    assert "service_catalog" in resource_fks


def test_resources_tags_gin_index_exists(engine):
    inspector = sa.inspect(engine)
    resource_indexes = {ix["name"] for ix in inspector.get_indexes("resources")}
    assert "ix_resources_tags_gin" in resource_indexes


def test_password_reset_tokens_never_store_plaintext(engine):
    inspector = sa.inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("password_reset_tokens")}
    assert "token_hash" in columns
    assert "token" not in columns


def test_phase2_unique_constraints_exist(engine):
    inspector = sa.inspect(engine)

    cost_uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("cloud_resource_costs")}
    assert ("provider", "source_record_key") in cost_uniques


def test_phase2_check_constraints_exist(engine):
    inspector = sa.inspect(engine)

    job_checks = {c["name"] for c in inspector.get_check_constraints("provisioning_jobs")}
    assert "ck_provisioning_jobs_progress_percent_range" in job_checks
    assert "ck_provisioning_jobs_created_resource_count_non_negative" in job_checks

    cost_checks = {c["name"] for c in inspector.get_check_constraints("cloud_resource_costs")}
    assert "ck_cloud_resource_costs_period_end_after_start" in cost_checks
    assert "ck_cloud_resource_costs_amount_non_negative" in cost_checks

    audit_checks = {c["name"] for c in inspector.get_check_constraints("audit_events")}
    assert "ck_audit_events_result" in audit_checks


def test_phase2_foreign_keys_exist(engine):
    inspector = sa.inspect(engine)

    cost_fks = {fk["referred_table"] for fk in inspector.get_foreign_keys("cloud_resource_costs")}
    assert "resources" in cost_fks

    audit_fks = {fk["referred_table"] for fk in inspector.get_foreign_keys("audit_events")}
    assert "users" in audit_fks


def test_reverted_entities_absent(engine):
    """이전 API 명세 유지 결정으로 제거된 스키마 조각이 없는지 확인."""
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert "resource_types" not in tables
    assert "provisioning_requests" not in tables

    job_columns = {c["name"] for c in inspector.get_columns("provisioning_jobs")}
    assert "provisioning_request_id" not in job_columns

    resource_columns = {c["name"] for c in inspector.get_columns("resources")}
    assert "resource_type_id" not in resource_columns


def test_phase2_key_indexes_exist(engine):
    inspector = sa.inspect(engine)

    audit_indexes = {ix["name"] for ix in inspector.get_indexes("audit_events")}
    assert "ix_audit_events_actor_created" in audit_indexes
    assert "ix_audit_events_target" in audit_indexes
    assert "ix_audit_events_action_created" in audit_indexes

    cost_indexes = {ix["name"] for ix in inspector.get_indexes("cloud_resource_costs")}
    assert "ix_cloud_resource_costs_resource_period" in cost_indexes
    assert "ix_cloud_resource_costs_provider_kind_period" in cost_indexes
