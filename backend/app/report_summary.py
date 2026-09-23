"""보고서 "AI 분석 요약" 섹션 — 생성 시점 실데이터 기반 LLM 요약(2026-09-23).

지금까지 프론트(`reports-data.js::BASE.summary`)가 고정 문구를 그대로 보여주고 있었다 —
`prod-api-01`, `Azure VM 2대` 등 사용자의 실제 리소스와 무관한 예시 텍스트였다(사용자 확인,
2026-09-23: "AI 분석 요약 섹션을 보면 내 인스턴스 데이터도 연동이 안 된 것 같다"). 이 모듈은
`app/report_cost.py`가 이미 계산한 비용 스냅샷과 `app/metrics.py`의 실시간 사용률·미사용
리소스 조회를 텍스트 컨텍스트로 묶어, `app/agent.py`가 쓰는 것과 같은 OpenAI Chat Completions
호출(같은 API 키·모델 설정, 새 의존성 없음)로 문단·조치 목록을 생성한다.

`cost_snapshot`과 같은 이유로 **생성 시점에 계산해서 고정 저장**한다(`app/routers/reports.py`의
`create_report_generation`이 생성/재생성 시에만 호출) — 사용률/미사용 리소스 자체는 조회
시점 실시간 값을 계속 쓰지만(과거 시점을 저장할 테이블이 없어서, `app/metrics.py` 참고), 이
요약 문단은 "그 순간의 상태에 대한 설명"이라 생성 시점 값을 그대로 묘사하는 편이 맞다 — 다시
열었을 때 사용률 표는 최신값인데 문단은 생성 당시 값을 설명해 서로 다른 시점을 가리킬 수 있다는
점은 `cost_snapshot`이 이미 받아들인 것과 같은 한계다.

`OPENAI_API_KEY` 미설정, LLM 호출 실패, 또는 응답 파싱 실패 시 예외를 그대로 올린다 —
호출자(`app/routers/reports.py`)가 `cost_snapshot`과 동일하게 잡아서 `ai_summary=None`으로
남기고 보고서 생성 자체는 계속 진행한다(장애 격리, §9). 목업으로 채우지 않는다.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.agent import call_chat_completion

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models import User

_MAX_ACTIONS = 4
_MAX_TOKENS = 700

_SYSTEM_PROMPT = """당신은 이 멀티클라우드 운영 대시보드의 보고서 작성 보조 도구입니다.
아래 "보고서 데이터"만 근거로 삼아 운영 보고서의 "AI 분석 요약" 섹션(문단 + 조치 목록)을
작성합니다.

중요한 제약:
- 반드시 JSON 객체 하나만 응답하세요. 형식: {{"paragraph": "...", "actions": ["...", "..."]}}
  다른 텍스트(설명, 코드블록 표시 등)를 앞뒤에 붙이지 마세요.
- paragraph는 2~4문장, 한국어로 비용 증감·주요 변동 원인·운영 이슈(사용률·미사용 리소스)를
  요약합니다.
- actions는 실행 가능한 제안을 최대 {max_actions}개, 각 항목은 한 문장입니다. 특별히 조치할
  것이 없으면 빈 배열을 반환하세요.
- "보고서 데이터"에 없는 리소스명·수치·기간을 지어내지 마세요. 근거가 부족한 항목은 언급하지
  마세요.
- 비용 수치는 이미 계산되어 있는 값을 그대로 인용만 하세요 — 직접 재계산하거나 서로 다른
  통화를 더하지 마세요. cost_kind가 "list_price_estimate"인 값과 "actual"인 값을 같은 것처럼
  섞지 마세요. comparability(=changes.comparable)가 false이면 "전 기간 대비 비교 불가"로
  말하고 임의로 증감률을 만들지 마세요.

보고서 데이터:
{context}
"""


class ReportSummaryParseError(Exception):
    """LLM 응답이 지정한 JSON 형식이 아니었을 때(모델이 지시를 어긴 경우)."""


def _cost_context(cost_snapshot: dict | None) -> str:
    if not cost_snapshot:
        return "비용 스냅샷 없음(생성 시점 계산 실패 — 비용 관련 언급은 하지 말 것)."
    # report-view.js가 실제로 화면에 쓰는 필드만 추린다 — breakdown_category의 rest/unallocated
    # 세부 항목 등은 요약 문단에 필요 없고 토큰만 늘린다.
    trimmed = {
        "summary": cost_snapshot.get("summary"),
        "changes": cost_snapshot.get("changes"),
        "breakdown_provider": cost_snapshot.get("breakdown_provider"),
    }
    return "비용 스냅샷(JSON, 생성 시점 고정값):\n" + json.dumps(trimmed, ensure_ascii=False, default=str)


def _ops_context(db: "Session", user: "User") -> str:
    # 지연 import — app/metrics.py가 app/providers/*를 끌어오는 무거운 임포트 체인이라, 이
    # 모듈을 import하는 것만으로 그 비용을 물지 않게 한다(app/report_cost.py의 카테고리 분류
    # 함수가 cost/query.py를 지연 import하는 것과 같은 이유).
    from app.metrics import get_top_utilization, get_unused_resources

    util = get_top_utilization(db, user, limit=5)
    unused = get_unused_resources(db, user, limit=5)

    lines = ["사용률 상위(실시간 조회, 생성 시점 기준 스냅샷):"]
    lines += [
        f"- [{u['provider']}] {u['name']}({u['original_resource_type']}) CPU="
        + (f"{u['cpu_percent']:.0f}%" if u["cpu_percent"] is not None else "조회 실패")
        for u in util
    ] if util else ["(없음)"]

    lines.append("미사용 리소스(실시간 조회, 생성 시점 기준 스냅샷):")
    lines += [
        f"- [{u['provider']}] {u['name']}({u['original_resource_type']}) 사유={u['reason']} 예상비용="
        + (f"${u['estimated_monthly_cost']:.2f}/mo" if u["estimated_monthly_cost"] is not None else "정보 없음")
        for u in unused
    ] if unused else ["(없음)"]

    return "\n".join(lines)


def _parse_response(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
        paragraph = str(parsed["paragraph"]).strip()
        actions = [str(a).strip() for a in (parsed.get("actions") or []) if str(a).strip()][:_MAX_ACTIONS]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ReportSummaryParseError(f"malformed ai summary response: {raw[:200]!r}") from exc
    if not paragraph:
        raise ReportSummaryParseError("empty paragraph in ai summary response")
    return {"paragraph": paragraph, "actions": actions}


async def build_ai_summary(db: "Session", user: "User", cost_snapshot: dict | None) -> dict:
    """생성 시점의 비용 스냅샷 + 실시간 사용률/미사용 리소스를 근거로 문단·조치 목록을 만든다.

    실패하면(`AgentNotConfiguredError`/`AgentUpstreamError`/`ReportSummaryParseError`) 예외를
    그대로 올린다 — 호출자가 `build_cost_snapshot()`과 같은 패턴으로 잡아서 `ai_summary=None`
    처리한다."""
    context = _cost_context(cost_snapshot) + "\n\n" + _ops_context(db, user)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT.format(context=context, max_actions=_MAX_ACTIONS)},
    ]
    raw = await call_chat_completion(messages, max_tokens=_MAX_TOKENS)
    return _parse_response(raw)
