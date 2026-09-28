"""최종발표 데모 계정(`demo@exam.com`) — 실제 CSP를 한 번도 부르지 않는 샌드박스.

우리 서비스는 사용자가 각 콘솔에서 IAM 권한을 설정해야 쓸 수 있어서, 발표장에서 다른 팀이 직접
써 보기 어렵다. 그래서 데모 계정 하나에 3사 데이터를 미리 심어 두고(`app/seed_demo_data.py`),
그 계정에서 누르는 버튼은 **CSP 대신 이 모듈이 응답**하게 한다. 데모 계정의 자격 증명은 전부
가짜 값이라 실제로 호출하면 인증 실패 → 리소스 stale 처리·수집 실패 배지로 화면이 망가진다.

- **판별은 서버가 아는 사용자 이메일로만 한다**: payload에 "demo" 표시를 넣는 방식은 쓰지 않는다 —
  일반 사용자가 등록 요청 payload에 같은 표시를 넣어 검증을 우회할 수 있기 때문이다.
- **비용은 날짜의 순수 함수다**(`demo_cost_rows`): 시드와 "수집" 버튼·자동 수집이 같은 함수를 부르므로
  같은 날짜는 언제 다시 만들어도 같은 금액이 나온다. 그래서 발표 날까지 매일 자동 수집이 돌면서
  어제 날짜 비용이 자연스럽게 채워지고, 화면이 "수집 지연"으로 바뀌지 않는다.
- 삭제·중지 같은 조작은 DB에만 반영한다. 원래대로 돌리려면 `python -m app.seed_demo_data`를
  다시 실행한다(데모 계정 데이터를 전부 지우고 새로 만든다).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.cost.base import CostFetchResult, CostRow
from app.cost.coverage import BASIS_COMPLETE_RANGE, utc_today
from app.errors import ApiError
from app.models import CloudAccount, User

DEMO_USER_EMAIL = "demo@exam.com"
DEMO_PASSWORD = "demo1234"  # 발표용 공개 계정 — 실제 사용자 비밀번호가 아니다.
DEMO_USER_NAME = "데모 사용자"

# cloud_account_costs.source. "seed"로 시작해야 급증 목록에 "예시 데이터" 배지가 붙는다(anomaly.py).
DEMO_COST_SOURCE = "seed_demo"
DEMO_ERROR_CODE = "DEMO_READ_ONLY"

# 비용 추세(완만한 증가)의 기준일. 고정값이라 언제 다시 계산해도 같은 날짜는 같은 금액이다.
_GROWTH_EPOCH = dt.date(2026, 1, 1)


# --- 판별 -------------------------------------------------------------------------------------


def is_demo_user(user: User | None) -> bool:
    return user is not None and user.normalized_email == DEMO_USER_EMAIL


def is_demo_user_id(db: Session, user_id: int | None) -> bool:
    if user_id is None:
        return False
    email = db.query(User.normalized_email).filter(User.id == user_id).scalar()
    return email == DEMO_USER_EMAIL


def is_demo_account(db: Session, account: CloudAccount | None) -> bool:
    return account is not None and is_demo_user_id(db, account.user_id)


def is_demo_email(email: str | None) -> bool:
    return (email or "").strip().lower() == DEMO_USER_EMAIL


def read_only_error(what: str) -> ApiError:
    return ApiError(403, DEMO_ERROR_CODE, f"데모 계정에서는 {what}할 수 없습니다.")


# --- 비용 생성기 --------------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoService:
    name: str
    base: Decimal  # 하루 기준 금액(계정 통화)
    growth_per_30d: float = 0.02  # 30일마다 늘어나는 비율
    weekend_dip: bool = False  # 주말에 사용량이 줄어드는 서비스(개발 환경 등)
    spike_mod: int | None = None  # 이 값으로 나눠떨어지는 날(해시 기준)에 급증
    starts_on: dt.date | None = None  # 이 날부터 비용 발생(기준선 0 → "신규 비용 발생" 급증)
    spike_days: tuple[dt.date, ...] = ()  # 해시와 별개로 반드시 급증하는 날(최근 예시가 늘 보이도록)


@dataclass(frozen=True)
class DemoAccountProfile:
    currency: str
    services: tuple[DemoService, ...]
    monthly_tax: Decimal | None = None  # 매월 1일 tax 행
    monthly_credit: Decimal | None = None  # 매월 1일 credit 행(음수로 저장)


# external_account_id → 비용 모양. 시드가 만드는 계정과 1:1이다(seed_demo_data.ACCOUNTS).
PROFILES: dict[str, DemoAccountProfile] = {
    "111122223333": DemoAccountProfile(  # prod-aws-01
        currency="USD",
        services=(
            DemoService("Amazon Elastic Compute Cloud - Compute", Decimal("6.44"), 0.03, spike_mod=41,
                        spike_days=(dt.date(2026, 9, 11),)),
            DemoService("EC2 - Other", Decimal("1.12")),
            DemoService("Amazon Relational Database Service", Decimal("3.36"), 0.01),
            DemoService("Amazon Simple Storage Service", Decimal("0.74"), 0.04),
            DemoService("Amazon CloudFront", Decimal("0.51")),
            DemoService("AmazonCloudWatch", Decimal("0.22")),
        ),
        monthly_tax=Decimal("13.30"),
        monthly_credit=Decimal("8.75"),
    ),
    "444455556666": DemoAccountProfile(  # stg-aws-02 (역할 위임 예시 계정)
        currency="USD",
        services=(
            DemoService("Amazon Elastic Compute Cloud - Compute", Decimal("2.20"), 0.02, weekend_dip=True),
            DemoService("Amazon Relational Database Service", Decimal("1.08"), weekend_dip=True),
            DemoService("Amazon Simple Storage Service", Decimal("0.28")),
            # 9/15부터 새로 켠 CDN — 기준선 0에서 시작해 "신규 비용 발생" 급증으로 잡힌다.
            DemoService("Amazon CloudFront", Decimal("7.20"), starts_on=dt.date(2026, 9, 15)),
        ),
    ),
    "00000000-1111-2222-3333-444455556666": DemoAccountProfile(  # prod-az-01
        currency="USD",
        services=(
            DemoService("Virtual Machines", Decimal("3.92"), 0.02),
            DemoService("SQL Database", Decimal("2.59"), 0.01, spike_mod=43, spike_days=(dt.date(2026, 9, 3),)),
            DemoService("Storage", Decimal("0.58"), 0.05),
            DemoService("Bandwidth", Decimal("0.32")),
            DemoService("Azure Front Door Service", Decimal("0.38")),
        ),
    ),
    "77777777-8888-9999-aaaa-bbbbccccdddd": DemoAccountProfile(  # dev-az-02 (원화 청구)
        currency="KRW",
        services=(
            DemoService("Virtual Machines", Decimal("9800"), 0.02, weekend_dip=True),
            DemoService("Azure Database for PostgreSQL", Decimal("6200"), weekend_dip=True),
            DemoService("Storage", Decimal("1500"), 0.03),
        ),
    ),
    "dev-gcp-01-project": DemoAccountProfile(  # dev-gcp-01
        currency="USD",
        services=(
            DemoService("Compute Engine", Decimal("2.73"), 0.02, weekend_dip=True),
            DemoService("Cloud Storage", Decimal("0.38"), 0.03),
            DemoService("Networking", Decimal("0.24")),
        ),
    ),
    "analytics-gcp-02": DemoAccountProfile(  # analytics-gcp-02
        currency="USD",
        services=(
            DemoService("BigQuery", Decimal("4.20"), 0.04, spike_mod=37, spike_days=(dt.date(2026, 9, 21),)),
            DemoService("Cloud SQL", Decimal("2.24"), 0.01),
            DemoService("Cloud Storage", Decimal("0.80"), 0.03),
            DemoService("Compute Engine", Decimal("1.44")),
        ),
    ),
}


def _unit(*parts: object) -> float:
    """문자열 해시로 만든 [0, 1) 값 — 난수 대신 써서 같은 입력이면 항상 같은 값이 나온다."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(digest[:12], 16) / float(1 << 48)


def _quant(amount: float, currency: str) -> Decimal:
    step = Decimal("1") if currency == "KRW" else Decimal("0.000001")
    return Decimal(str(amount)).quantize(step)


def daily_amount(external_account_id: str, service: DemoService, day: dt.date) -> Decimal | None:
    """그 날 그 서비스 비용. 발생 전이면 None(행을 만들지 않는다)."""
    if service.starts_on and day < service.starts_on:
        return None
    months = (day - _GROWTH_EPOCH).days / 30
    amount = float(service.base) * (1 + service.growth_per_30d * months)
    if service.weekend_dip and day.weekday() >= 5:
        amount *= 0.55
    amount *= 0.9 + 0.2 * _unit(external_account_id, service.name, day, "noise")  # ±10%
    # 급증 규칙(직전 7일 평균 대비 +50% · +$5 이상)을 확실히 넘도록 3배 이상으로 튄다.
    hashed = service.spike_mod and int(_unit(external_account_id, service.name, day, "spike") * 10_000) % service.spike_mod == 0
    if hashed or day in service.spike_days:
        amount = amount * 3.2 + 6
    return _quant(amount, PROFILES[external_account_id].currency)


def demo_cost_rows(external_account_id: str, start: dt.date, end: dt.date) -> list[CostRow]:
    """[start, min(end, 오늘)) 구간의 비용 행. 오늘(UTC)은 아직 끝나지 않은 날이라 만들지 않는다."""
    profile = PROFILES.get(external_account_id)
    if profile is None:
        return []
    stop = min(end, utc_today())
    rows: list[CostRow] = []
    day = start
    while day < stop:
        nxt = day + dt.timedelta(days=1)
        for service in profile.services:
            amount = daily_amount(external_account_id, service, day)
            if amount is None:
                continue
            rows.append(CostRow(
                period_start=day, period_end=nxt, service=service.name, charge_category="usage",
                amount=amount, currency=profile.currency, is_estimated=False,
                source_record_key=f"demo:{external_account_id}:{service.name}:{day.isoformat()}:usage",
            ))
        if day.day == 1:
            if profile.monthly_tax:
                rows.append(CostRow(
                    period_start=day, period_end=nxt, service="Tax", charge_category="tax",
                    amount=profile.monthly_tax, currency=profile.currency, is_estimated=False,
                    source_record_key=f"demo:{external_account_id}:Tax:{day.isoformat()}:tax",
                ))
            if profile.monthly_credit:
                rows.append(CostRow(
                    period_start=day, period_end=nxt, service=None, charge_category="credit",
                    amount=-profile.monthly_credit, currency=profile.currency, is_estimated=False,
                    source_record_key=f"demo:{external_account_id}:Credit:{day.isoformat()}:credit",
                ))
        day = nxt
    return rows


class DemoCostProvider:
    """`CostProvider`와 같은 모양 — 수동·자동 수집 경로가 실제 어댑터 대신 이것을 쓴다."""

    def fetch(self, secret_payload: dict, external_account_id: str, period_start: dt.date, period_end: dt.date) -> CostFetchResult:
        rows = demo_cost_rows(external_account_id, period_start, period_end)
        profile = PROFILES.get(external_account_id)
        return CostFetchResult(
            rows=rows,
            currency=profile.currency if profile else None,
            covered_through=min(period_end, utc_today()) - dt.timedelta(days=1),
            api_calls=0,
            coverage_basis=BASIS_COMPLETE_RANGE,
        )


# --- 그 밖의 가짜 CSP 응답 ----------------------------------------------------------------------


def cpu_percent(external_resource_id: str) -> float:
    """대시보드·보고서 "사용률 상위/유휴"용 CPU(%). 리소스마다 고정값에 가깝게, 시간대별로 조금 흔든다."""
    base = 3 + 85 * _unit(external_resource_id, "cpu")
    hour = dt.datetime.now(dt.timezone.utc).hour
    wiggle = 6 * (_unit(external_resource_id, "cpu", hour) - 0.5)
    return round(max(0.5, min(99.0, base + wiggle)), 1)


PROVISIONING_DELAY_SECONDS = 6  # 진행률 모달이 "진행 중"을 한 번은 보여 주도록


def fake_terraform_outputs(provider: str, service_code: str, job_id: int, common_spec: dict, provider_spec: dict) -> dict:
    """`routers/provisioning.py::_resource_attrs`가 읽는 output 키를 서비스별로 채운다."""
    name = (common_spec.get("name") or f"demo-{job_id}").strip()
    region = provider_spec.get("region")
    suffix = hashlib.sha256(f"{job_id}".encode()).hexdigest()[:8]
    if provider == "aws":
        return {
            "s3": {"bucket_name": name},
            "cloudfront": {"distribution_id": f"E{suffix.upper()}DEMO", "domain_name": f"d{suffix}.cloudfront.net"},
            "rds": {"db_instance_id": name, "endpoint": f"{name}.{suffix}.{region}.rds.amazonaws.com"},
        }.get(service_code, {"instance_id": f"i-0{suffix}demo{job_id:04d}"[:19], "public_ip": "203.0.113.10"})
    if provider == "gcp":
        return {
            "cloud_sql": {"instance_name": name, "region": region},
            "cloud_storage": {"bucket_name": name, "region": region},
            "cloud_cdn": {"forwarding_rule_name": f"{name}-fr", "ip_address": "203.0.113.20"},
        }.get(service_code, {"instance_name": name, "zone": f"{region}-a" if region else "asia-northeast3-a"})
    if provider == "azure":
        sub = "00000000-1111-2222-3333-444455556666"
        return {
            "sql_database": {"server_name": name},
            "storage_account": {"account_name": name.replace("-", "")[:24]},
            "cdn": {"profile_name": name},
        }.get(service_code, {
            "vm_id": f"/subscriptions/{sub}/resourceGroups/rg-demo/providers/Microsoft.Compute/virtualMachines/{name}",
        })
    return {}


def wait_like_terraform() -> None:
    time.sleep(PROVISIONING_DELAY_SECONDS)
