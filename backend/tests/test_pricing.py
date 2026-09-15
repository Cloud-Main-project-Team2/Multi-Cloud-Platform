"""`app/pricing.py` 단위 검증 — 정가 기반 월 예상 비용 추정."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.pricing import estimate_monthly_cost_usd


@pytest.mark.parametrize(
    "provider,service_code,provider_spec,expected",
    [
        ("aws", "ec2", {"instance_type": "t3.micro", "region": "us-east-1"}, Decimal("7.59")),
        ("aws", "ec2", {"instance_type": "t3.medium", "region": "ap-northeast-2"}, Decimal("37.96")),
        ("aws", "rds", {"instance_class": "db.t3.small", "region": "us-east-1"}, Decimal("24.82")),
        ("azure", "vm", {"instance_type": "B2s", "region": "koreacentral"}, Decimal("41.03")),
        ("azure", "sql_database", {"engine": "MySQL", "region": "eastus"}, Decimal("11.32")),
        ("gcp", "compute_engine", {"instance_type": "e2-medium", "region": "us-central1"}, Decimal("24.46")),
        ("gcp", "cloud_sql", {"engine": "PostgreSQL", "region": "asia-northeast3"}, Decimal("46.06")),
    ],
)
def test_estimate_monthly_cost_usd_known_sku(provider, service_code, provider_spec, expected):
    assert estimate_monthly_cost_usd(provider, service_code, provider_spec) == expected


@pytest.mark.parametrize(
    "provider,service_code,provider_spec",
    [
        # 사용량 기반 서비스 — 정가표 대상 아님
        ("aws", "s3", {"region": "us-east-1"}),
        ("aws", "cloudfront", {"origin_domain_name": "example.com"}),
        ("azure", "storage_account", {"region": "eastus"}),
        ("gcp", "cloud_storage", {"region": "us-central1"}),
        ("gcp", "cloud_cdn", {"origin_domain_name": "example.com"}),
        # 허용 목록 밖 스펙/리전 — 값을 지어내지 않고 None
        ("aws", "ec2", {"instance_type": "t3.large", "region": "us-east-1"}),
        ("aws", "ec2", {"instance_type": "t3.micro", "region": "eu-west-1"}),
        ("azure", "vm", {"instance_type": "Standard_B1s", "region": "koreacentral"}),  # 정규화 전 값만 인식
        ("azure", "sql_database", {"engine": "Oracle", "region": "eastus"}),
        ("gcp", "compute_engine", {"instance_type": "e2-micro", "region": "asia-northeast1"}),
    ],
)
def test_estimate_monthly_cost_usd_unknown_returns_none(provider, service_code, provider_spec):
    assert estimate_monthly_cost_usd(provider, service_code, provider_spec) is None


def test_estimate_monthly_cost_usd_missing_region_returns_none():
    assert estimate_monthly_cost_usd("aws", "ec2", {"instance_type": "t3.micro"}) is None
