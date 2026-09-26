"""검증 환경용 **최소 모의 데이터**(비밀값 없음, 자격증명 없음).

`app/seed_mock_data.py`는 쓰지 않는다 — 그쪽은 **모의 자격증명까지 만들고**(암호화 payload) 검증
상태까지 넣기 때문에, "자격증명 없는 최소 시드"라고 부르면 사실과 다르다. 여기서는 화면을 보는 데
필요한 것만 만든다: 데모 사용자 1 · 모의 클라우드 계정 2 · 팀 2 · 예산 2 · 일별 비용 행 · 수집 run 2.

만드는 데이터는 **근거(coverage_basis)만 다른 대조군**이다 — 날짜·금액은 같다.
  ① azure 계정(observed_only) → 예산 COVERAGE_UNVERIFIED · 급증 보류
  ② aws   계정(complete_range) → 예산 소진율 계산 · 급증 1건 탐지

안전장치: 접속한 DB 이름이 `mcp_db_verify`로 시작하지 않으면 **아무것도 하지 않고 종료**한다.
재실행해도 같은 행을 다시 만들지 않는다(멱등).

실행(검증 프로필 안에서):
  docker --context colima-verify run --rm --network mcp-verify-net \
    -v "$PWD/scripts:/seed:ro" -e DATABASE_URL=... --entrypoint "" mcp-verify-api:local \
    python /seed/verify_seed_min_data.py
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    CloudAccount,
    CloudAccountCost,
    CostIngestionRun,
    Team,
    TeamBudget,
    User,
)
from app.security.passwords import hash_password

DEMO_EMAIL = "demo@multicloud.example"
DEMO_PASSWORD = "demo-pass-1234"      # 데모 계정 — 실제 사용자 비밀번호가 아니다
PERIOD_START = dt.date(2026, 9, 1)
PERIOD_END = dt.date(2026, 9, 24)     # 제외 경계
SPIKE_DAY = dt.date(2026, 9, 18)
NORMAL_AMOUNT = Decimal("10.000000")
SPIKE_AMOUNT = Decimal("300.000000")
BUDGET_LIMIT = Decimal("600.000000")

ACCOUNTS = [
    # (provider, external_account_id, label, 팀 이름, 수집 근거, 서비스명)
    ("azure", "verify-az-usd-0001", "verify-az-usd", "verify-usd-azure", "observed_only", "Virtual Machines"),
    ("aws", "999988887777", "verify-aws-usd", "verify-usd-aws", "complete_range",
     "Amazon Elastic Compute Cloud - Compute"),
]
SOURCE = {"azure": "azure_cost_management", "aws": "aws_cost_explorer"}


def guard(db) -> None:
    name = get_settings().database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not name.startswith("mcp_db_verify"):
        print(f"거부: 검증용 DB가 아닙니다 → {name}")
        sys.exit(2)
    print(f"대상 DB: {name}")


def get_or_create_user(db) -> User:
    user = db.scalar(select(User).where(User.normalized_email == DEMO_EMAIL.lower()))
    if user:
        return user
    user = User(
        email=DEMO_EMAIL, normalized_email=DEMO_EMAIL.lower(), password_hash=hash_password(DEMO_PASSWORD),
        name="데모 사용자", affiliation_type="individual", affiliation_name=None, status="active",
    )
    db.add(user)
    db.flush()
    return user


def main() -> int:
    created = {"user": 0, "team": 0, "account": 0, "budget": 0, "cost_row": 0, "run": 0}
    now = dt.datetime.now(dt.timezone.utc)
    with SessionLocal() as db:
        guard(db)
        before = db.scalar(select(User).where(User.normalized_email == DEMO_EMAIL.lower()))
        user = get_or_create_user(db)
        created["user"] = 0 if before else 1

        for provider, external_id, label, team_name, basis, service in ACCOUNTS:
            team = db.scalar(select(Team).where(Team.user_id == user.id, Team.name == team_name))
            if team is None:
                team = Team(user_id=user.id, name=team_name, currency="USD")
                db.add(team)
                db.flush()
                created["team"] += 1

            account = db.scalar(select(CloudAccount).where(
                CloudAccount.user_id == user.id, CloudAccount.provider == provider,
                CloudAccount.external_account_id == external_id))
            if account is None:
                account = CloudAccount(user_id=user.id, provider=provider, external_account_id=external_id,
                                       account_label=label, team_id=team.id)
                db.add(account)
                db.flush()
                created["account"] += 1
            elif account.team_id != team.id:
                account.team_id = team.id

            if db.scalar(select(TeamBudget).where(TeamBudget.team_id == team.id)) is None:
                db.add(TeamBudget(team_id=team.id, period_type="monthly", start_date=PERIOD_START,
                                  end_date=None, limit_amount=BUDGET_LIMIT, currency="USD"))
                created["budget"] += 1

            day = PERIOD_START
            while day < PERIOD_END:
                key = f"verify-ba:{account.id}:{day.isoformat()}"
                exists = db.scalar(select(CloudAccountCost).where(CloudAccountCost.source_record_key == key))
                if exists is None:
                    db.add(CloudAccountCost(
                        cloud_account_id=account.id, provider=provider, cost_kind="actual",
                        charge_category="usage", service=service,
                        amount=SPIKE_AMOUNT if day == SPIKE_DAY else NORMAL_AMOUNT, currency="USD",
                        period_start=day, period_end=day + dt.timedelta(days=1),
                        is_estimated=(provider == "azure"), as_of=now, source=SOURCE[provider],
                        source_record_key=key))
                    created["cost_row"] += 1
                day += dt.timedelta(days=1)

            run_exists = db.scalar(select(CostIngestionRun).where(
                CostIngestionRun.cloud_account_id == account.id,
                CostIngestionRun.error_message == "verify_seed_min_data"))
            if run_exists is None:
                db.add(CostIngestionRun(
                    user_id=user.id, cloud_account_id=account.id, trigger_type="auto", status="success",
                    period_start=PERIOD_START, period_end=PERIOD_END, requested_at=now, started_at=now,
                    finished_at=now, api_calls=1, records_replaced=(PERIOD_END - PERIOD_START).days,
                    error_message="verify_seed_min_data", coverage_basis=basis))
                created["run"] += 1
        db.commit()

    print("새로 만든 행:", ", ".join(f"{k}={v}" for k, v in created.items()))
    print("로그인: " + DEMO_EMAIL + " (비밀번호는 이 파일 상단 상수 — 데모 전용)")
    print("자격증명(credentials) 행은 만들지 않는다 — 실제 CSP 호출 경로는 열리지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
