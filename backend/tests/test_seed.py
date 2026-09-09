from app.models import ServiceCatalog
from app.seed import SERVICE_CATALOG_SEED, seed_service_catalog


def test_seed_creates_exactly_twelve_rows_and_is_idempotent(db_session):
    seed_service_catalog(db_session, rows=SERVICE_CATALOG_SEED)
    seed_service_catalog(db_session, rows=SERVICE_CATALOG_SEED)
    db_session.flush()

    assert db_session.query(ServiceCatalog).count() == 12


def test_seed_upserts_changed_values_without_duplicating(db_session):
    seed_service_catalog(db_session, rows=SERVICE_CATALOG_SEED)
    db_session.flush()

    changed_row = dict(SERVICE_CATALOG_SEED[0])
    changed_row["display_name"] = "EC2 (변경됨)"
    changed_row["category"] = "compute_changed"
    changed_row["provisionable"] = False

    seed_service_catalog(db_session, rows=[changed_row])
    db_session.flush()

    row = (
        db_session.query(ServiceCatalog)
        .filter_by(provider=changed_row["provider"], service_code=changed_row["service_code"])
        .one()
    )
    assert row.display_name == "EC2 (변경됨)"
    assert row.category == "compute_changed"
    assert row.provisionable is False
    assert db_session.query(ServiceCatalog).count() == 12
