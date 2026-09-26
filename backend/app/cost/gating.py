"""실측 비용 수집 **활성화 제어** — "어댑터가 있다"와 "이 CSP를 실제로 호출한다"를 분리한다.

세 가지는 서로 다른 질문이고, 이 모듈은 ②③만 답한다.

| # | 질문 | 판정하는 곳 |
|---|---|---|
| ① | 이 CSP의 수집기가 **구현**돼 있는가 | `app/cost/__init__.py::is_cost_supported` (→ `UNSUPPORTED`) |
| ② | 지금 이 계정을 **수동으로** 수집해도 되는가 | 이 모듈 `manual_ingest_denial()` |
| ③ | 지금 이 계정을 **자동으로** 수집해도 되는가 | 이 모듈 `auto_ingest_allowed()` |

왜 필요한가: `COST_ADAPTERS`에 provider 한 줄을 추가하면 지금까지는 **세 가지가 동시에 켜졌다** —
capabilities가 지원으로 바뀌고, 수동 수집 대상이 되고, `scheduler.run_daily_ingestion()`이
`provider.in_(COST_ADAPTERS)`로 계정을 뽑아 **등록된 모든 계정을 매일 호출**했다. 새 CSP를 붙이는
동안에는 "구현은 됐지만 아직 아무 계정도 호출하지 않는" 상태가 있어야 한다.

설계 원칙

- **기본값은 꺼짐**: `COST_INGEST_PROVIDERS`·`COST_AUTO_INGEST_PROVIDERS` 기본값이 `aws`뿐이라
  Azure·GCP 어댑터가 등록돼도 호출이 0건이다. 기존 AWS 동작은 그대로다.
- **수동과 자동은 독립**: 테스트 계정 하나를 수동으로 허용해도 자동은 꺼진 채로 둔다.
- **검증 전 CSP는 계정을 명시해야 켜진다**: `ACCOUNT_SCOPED_PROVIDERS`에 든 provider는
  `COST_INGEST_ACCOUNT_IDS`에 적힌 계정만 대상이다. **목록이 비면 한 계정도 허용하지 않는다**
  (빈 목록을 "전체 허용"으로 읽으면 실수 한 번에 전 계정이 과금 호출로 열린다).
- **잘못된 설정은 넓히지 않는다**: 알 수 없는 provider 이름·형식이 틀린 항목·숫자가 아닌 계정 id는
  **무시하고 경고 로그만** 남긴다. 파싱 실패가 "전체 허용"이 되는 경로는 만들지 않는다.
- **소유권 검사를 대체하지 않는다**: 이 판정은 라우터가 `CloudAccount.user_id == current_user.id`로
  이미 거른 계정에만 적용한다. 허용 목록에 남의 계정 id를 적어도 그 사용자의 요청이 아니면 닿지 않는다.
- **끈다고 과거 데이터가 사라지지 않는다**: 저장된 `cloud_account_costs`·마지막 성공 run은 그대로이고
  조회 API·집계는 이 모듈을 보지 않는다. 여기서 막는 것은 **새 CSP 호출**뿐이다.
"""

from __future__ import annotations

from app.config import get_settings
from app.logging_config import log_business_event

# 우리가 아는 provider 이름(오타를 넓힘이 아니라 무시로 처리하기 위한 목록).
KNOWN_PROVIDERS = frozenset({"aws", "azure", "gcp"})

# 계정 단위 허용 목록을 **반드시** 요구하는 provider — 아직 실 수집으로 검증되지 않은 CSP.
# 검증이 끝나면 이 집합에서 빼고(코드 리뷰로 드러나게) 전체 계정을 열 수 있다.
ACCOUNT_SCOPED_PROVIDERS = frozenset({"azure", "gcp"})

# skipped[].reason_code — 05 §5-1 계약에 추가한 값(프론트 SKIP_REASON에도 같은 키를 둔다).
REASON_INGEST_DISABLED = "INGEST_DISABLED"          # 이 CSP의 수집이 꺼져 있다(구현은 돼 있다)
REASON_ACCOUNT_NOT_ENABLED = "ACCOUNT_NOT_ENABLED"  # CSP는 켜졌지만 이 계정이 허용 목록에 없다


def _parse_providers(raw: str, *, setting: str) -> frozenset[str]:
    out: set[str] = set()
    for chunk in (raw or "").split(","):
        name = chunk.strip().lower()
        if not name:
            continue
        if name not in KNOWN_PROVIDERS:
            log_business_event("cost.gating.unknown_provider", level="WARNING", setting=setting, value=name)
            continue
        out.add(name)
    return frozenset(out)


def _parse_account_ids(raw: str) -> dict[str, frozenset[int]]:
    """`"azure:42,gcp:7"` → `{"azure": {42}, "gcp": {7}}`. 형식이 틀린 항목은 무시하고 경고만 남긴다."""
    acc: dict[str, set[int]] = {}
    for chunk in (raw or "").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        provider, sep, value = entry.partition(":")
        provider = provider.strip().lower()
        if not sep or provider not in KNOWN_PROVIDERS:
            log_business_event("cost.gating.invalid_account_entry", level="WARNING", entry=entry)
            continue
        try:
            account_id = int(value.strip())
        except ValueError:
            log_business_event("cost.gating.invalid_account_entry", level="WARNING", entry=entry)
            continue
        acc.setdefault(provider, set()).add(account_id)
    return {p: frozenset(ids) for p, ids in acc.items()}


def manual_ingest_providers() -> frozenset[str]:
    return _parse_providers(get_settings().cost_ingest_providers, setting="COST_INGEST_PROVIDERS")


def auto_ingest_providers() -> frozenset[str]:
    return _parse_providers(get_settings().cost_auto_ingest_providers, setting="COST_AUTO_INGEST_PROVIDERS")


def allowed_account_ids(provider: str) -> frozenset[int] | None:
    """계정 단위 제한이 걸린 provider면 허용 계정 집합, 제한이 없는 provider면 `None`.

    `None`은 "계정 제한 없음"이고 `frozenset()`은 "한 계정도 허용 안 함"이다 — 둘을 섞지 않는다."""
    if provider not in ACCOUNT_SCOPED_PROVIDERS:
        return None
    return _parse_account_ids(get_settings().cost_ingest_account_ids).get(provider, frozenset())


def _denial(provider: str, cloud_account_id: int, enabled: frozenset[str]) -> str | None:
    if provider not in enabled:
        return REASON_INGEST_DISABLED
    allowed = allowed_account_ids(provider)
    if allowed is not None and cloud_account_id not in allowed:
        return REASON_ACCOUNT_NOT_ENABLED
    return None


def manual_ingest_denial(provider: str, cloud_account_id: int) -> str | None:
    """수동 수집을 막아야 하면 `reason_code`, 허용이면 `None`."""
    return _denial(provider, cloud_account_id, manual_ingest_providers())


def auto_ingest_allowed(provider: str, cloud_account_id: int) -> bool:
    """자동 수집(스케줄러) 대상인지. 수동 허용 여부와 무관하게 별도로 판정한다."""
    return _denial(provider, cloud_account_id, auto_ingest_providers()) is None
