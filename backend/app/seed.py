from collections.abc import Sequence
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import ServiceCatalog

SERVICE_CATALOG_SEED: list[dict[str, Any]] = [
    {"provider": "aws", "service_code": "ec2", "category": "compute", "display_name": "EC2", "provisionable": True},
    {"provider": "aws", "service_code": "rds", "category": "db_rdbms", "display_name": "RDS", "provisionable": True},
    {"provider": "aws", "service_code": "s3", "category": "storage_object", "display_name": "S3", "provisionable": True},
    {"provider": "aws", "service_code": "cloudfront", "category": "cdn", "display_name": "CloudFront", "provisionable": True},
    {"provider": "azure", "service_code": "vm", "category": "compute", "display_name": "Virtual Machine", "provisionable": True},
    {"provider": "azure", "service_code": "sql_database", "category": "db_rdbms", "display_name": "SQL Database", "provisionable": True},
    {"provider": "azure", "service_code": "storage_account", "category": "storage_object", "display_name": "Storage Account", "provisionable": True},
    {"provider": "azure", "service_code": "cdn", "category": "cdn", "display_name": "Azure CDN", "provisionable": True},
    {"provider": "gcp", "service_code": "compute_engine", "category": "compute", "display_name": "Compute Engine", "provisionable": True},
    {"provider": "gcp", "service_code": "cloud_sql", "category": "db_rdbms", "display_name": "Cloud SQL", "provisionable": True},
    {"provider": "gcp", "service_code": "cloud_storage", "category": "storage_object", "display_name": "Cloud Storage", "provisionable": True},
    {"provider": "gcp", "service_code": "cloud_cdn", "category": "cdn", "display_name": "Cloud CDN", "provisionable": True},
]


def seed_service_catalog(db: Session, rows: Sequence[dict[str, Any]] = SERVICE_CATALOG_SEED) -> None:
    """Idempotently upsert service_catalog rows. Caller controls commit."""
    stmt = pg_insert(ServiceCatalog).values(list(rows))
    stmt = stmt.on_conflict_do_update(
        index_elements=[ServiceCatalog.provider, ServiceCatalog.service_code],
        set_={
            "category": stmt.excluded.category,
            "display_name": stmt.excluded.display_name,
            "provisionable": stmt.excluded.provisionable,
        },
    )
    db.execute(stmt)


def main() -> None:
    from app.db import SessionLocal

    with SessionLocal() as db:
        seed_service_catalog(db)
        db.commit()


if __name__ == "__main__":
    main()
