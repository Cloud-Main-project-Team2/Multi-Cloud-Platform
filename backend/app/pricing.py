"""정가(list price) 기반 리소스 비용 추정 — 대시보드 "예상 비용" 카드용.

실제 CSP 비용 API(AWS Cost Explorer/Azure Cost Management/GCP Billing) 연동은 계정별 권한·
비용 할당 태그 활성화 등으로 범위가 커서(§19 "GCP 실제 비용 수집 방식·권한" 미결정) 이번엔
건너뛴다. 대신 우리가 이미 강제하는 allow-list(instance_type/instance_class/engine)를 그대로
정가표 키로 써서, CSP API를 전혀 호출하지 않고 "이 스펙이면 대략 얼마"를 계산한다.

`cloud_resource_costs.cost_kind`의 세 값(actual/estimated/list_price_estimate) 중
`list_price_estimate`에 해당한다 — 실측이 아니라 정가 기준 추정치임을 호출부가 명확히 표시해야
한다(`app/routers/provisioning.py`의 `_create_resource_from_job` 참고).

**가격 출처/한계**: 2026-09 시점에 공개 가격 페이지·집계 사이트를 참고해 근사한 값이다. 리전별
정확한 공식 단가는 주기적으로 각 CSP 가격 계산기로 재검증해야 한다 — 여기 숫자를 실측치로 취급하지
않는다. Storage/CDN(S3/Storage Account/Cloud Storage/CloudFront/Cloud CDN)처럼 사용량 기반(GB·
요청 수) 서비스는 실제 사용량을 모르는 채로 숫자를 지어내는 셈이라 아예 추정하지 않는다 —
`estimate_monthly_cost_usd()`가 이런 조합에는 None을 반환한다.

provider_spec 필드명은 각 러너가 실제로 쓰는 것과 정확히 같다:
- AWS EC2: `instance_type`, `region` (aws_provisioning.ALLOWED_INSTANCE_TYPES/ALLOWED_REGIONS)
- AWS RDS: `instance_class`, `region` (aws_rds_provisioning.ALLOWED_INSTANCE_CLASSES/ALLOWED_REGIONS)
- Azure VM: `instance_type`, `region` — DB엔 프론트가 보낸 원본값("B1s" 등, "Standard_" 접두사
  없음)이 저장된다. 정규화(`Standard_B1s`)는 azure_provisioning.py의 pydantic validator가 실행
  시점에만 하고 spec_json에는 반영되지 않는다.
- Azure DB: `engine`("MySQL"/"PostgreSQL"/"SQL Server"), `region` — SKU는 엔진별로 고정이라
  사용자가 고르지 않는다(azure_database_provisioning.py 참고).
- GCP Compute Engine: `instance_type`, `region` (gcp_provisioning.ALLOWED_MACHINE_TYPES/REGIONS).
- GCP Cloud SQL: `engine`("MySQL"/"PostgreSQL"/"SQL Server"), `region` — SKU도 엔진별 고정
  (gcp_cloudsql_provisioning._ENGINE_CONFIG 참고).
"""

from __future__ import annotations

from decimal import Decimal

HOURS_PER_MONTH = Decimal("730")

# provider -> service_code -> instance_type/machine_type -> region -> USD/시간.
_COMPUTE_HOURLY_USD: dict[str, dict[str, dict[str, dict[str, str]]]] = {
    "aws": {
        "ec2": {
            "t3.micro": {"ap-northeast-2": "0.0130", "us-east-1": "0.0104"},
            "t3.small": {"ap-northeast-2": "0.0260", "us-east-1": "0.0208"},
            "t3.medium": {"ap-northeast-2": "0.0520", "us-east-1": "0.0416"},
        },
    },
    "azure": {
        "vm": {
            "B1s": {"koreacentral": "0.0140", "koreasouth": "0.0140", "eastus": "0.0104", "canadacentral": "0.0109"},
            "B2s": {"koreacentral": "0.0562", "koreasouth": "0.0562", "eastus": "0.0416", "canadacentral": "0.0437"},
            "B4ms": {"koreacentral": "0.2241", "koreasouth": "0.2241", "eastus": "0.1660", "canadacentral": "0.1743"},
        },
    },
    "gcp": {
        "compute_engine": {
            "e2-micro": {"asia-northeast3": "0.0101", "us-central1": "0.0084"},
            "e2-medium": {"asia-northeast3": "0.0402", "us-central1": "0.0335"},
            "e2-standard-4": {"asia-northeast3": "0.1608", "us-central1": "0.1340"},
        },
    },
}

# AWS RDS는 instance_class 하나로 조회한다(engine별 가격 차이는 근사 범위 밖 — mysql/postgres를
# 같은 값으로 취급한다. 실제로는 postgres가 소폭 더 비싸다).
_AWS_RDS_HOURLY_USD: dict[str, dict[str, str]] = {
    "db.t3.micro": {"ap-northeast-2": "0.0210", "us-east-1": "0.0170"},
    "db.t3.small": {"ap-northeast-2": "0.0420", "us-east-1": "0.0340"},
    "db.t3.medium": {"ap-northeast-2": "0.0880", "us-east-1": "0.0710"},
}

# Azure Database/GCP Cloud SQL은 엔진별 SKU가 고정이라(사용자가 사양을 고르지 않음) engine으로
# 바로 조회한다. 키 표기("MySQL"/"PostgreSQL"/"SQL Server")는 두 러너가 이미 쓰는 것과 동일하다.
_DB_HOURLY_USD_BY_ENGINE: dict[str, dict[str, dict[str, str]]] = {
    "azure": {
        "MySQL": {"koreacentral": "0.0205", "koreasouth": "0.0205", "eastus": "0.0155", "canadacentral": "0.0163"},
        "PostgreSQL": {"koreacentral": "0.0246", "koreasouth": "0.0246", "eastus": "0.0186", "canadacentral": "0.0195"},
        "SQL Server": {"koreacentral": "0.0068", "koreasouth": "0.0068", "eastus": "0.0068", "canadacentral": "0.0068"},
    },
    "gcp": {
        "MySQL": {"asia-northeast3": "0.0151", "us-central1": "0.0126"},
        "PostgreSQL": {"asia-northeast3": "0.0631", "us-central1": "0.0526"},
        "SQL Server": {"asia-northeast3": "0.0631", "us-central1": "0.0526"},
    },
}

_DB_SERVICE_CODES = {"sql_database", "cloud_sql"}


def _hourly_to_monthly(hourly: str) -> Decimal:
    return (Decimal(hourly) * HOURS_PER_MONTH).quantize(Decimal("0.01"))


def estimate_monthly_cost_usd(provider: str, service_code: str, provider_spec: dict) -> Decimal | None:
    """`(provider, service_code)` + provider_spec으로 정가 기반 월 예상 비용(USD)을 추정한다.

    가격표에 없는 조합(사용량 기반 서비스, 허용 목록 밖 스펙/리전)은 전부 None을 반환한다 —
    모르는 값에 임의의 숫자를 지어내지 않는다.
    """
    region = provider_spec.get("region")

    compute_table = _COMPUTE_HOURLY_USD.get(provider, {}).get(service_code)
    if compute_table is not None:
        sku = provider_spec.get("instance_type")
        hourly = compute_table.get(sku, {}).get(region)
        return _hourly_to_monthly(hourly) if hourly else None

    if provider == "aws" and service_code == "rds":
        sku = provider_spec.get("instance_class")
        hourly = _AWS_RDS_HOURLY_USD.get(sku, {}).get(region)
        return _hourly_to_monthly(hourly) if hourly else None

    if service_code in _DB_SERVICE_CODES:
        engine = provider_spec.get("engine")
        hourly = _DB_HOURLY_USD_BY_ENGINE.get(provider, {}).get(engine, {}).get(region)
        return _hourly_to_monthly(hourly) if hourly else None

    return None
