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
