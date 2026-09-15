"""AI 비용 어시스턴트 — `app/routers/agent.py`가 호출하는 컨텍스트 조립 + LLM 연동.

`docs/design/확장기능/agent1.png`/`agent2.png` 목업의 "플랫폼 비용을 비교하고, 절감 방법을
찾아보세요" 채팅 인터페이스를 실제로 동작하게 만든다. 실제 CSP 비용 API(Cost Explorer 등)
연동은 아직 없어서, 지금 가진 데이터 — `/resources`의 리소스 목록·`app/pricing.py`가 채운 정가
기반 추정 비용·최근 프로비저닝 이력 — 를 LLM 컨텍스트로 그대로 넣는다. 나중에 실측 비용 수집이
붙어도 `build_user_context()`만 그 데이터를 추가로 읽게 고치면 되고, 채팅 자체의 구조(엔드포인트,
프론트 UI)는 안 바뀐다.

OpenAI Chat Completions API를 `httpx`로 직접 호출한다(2026-09-15: 팀이 실제로 발급받은 키가
Anthropic이 아니라 OpenAI라 이 API로 맞췄다) — 공식 `openai` SDK를 새 의존성으로 추가하지 않기
위해서다(`httpx`는 이미 이 프로젝트의 기존 의존성).
"""

from __future__ import annotations

from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CloudAccount, ProvisioningJob, Resource, ServiceCatalog

_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
_MAX_TOKENS = 1024
_MAX_RESOURCE_LINES = 30
_MAX_JOB_LINES = 10

_SYSTEM_PROMPT_TEMPLATE = """당신은 이 멀티클라우드 운영 대시보드에 내장된 비용 어시스턴트입니다.
사용자가 AWS/Azure/GCP에 걸쳐 만든 리소스와 예상 비용을 바탕으로 절감 방법, 플랫폼 간 비교,
운영 관련 질문에 답합니다.

중요한 제약:
- 아래 "현재 계정 현황"의 비용 숫자는 실제 CSP 비용 API(Cost Explorer/Cost Management/Billing 등)
  실측치가 아니라, 인스턴스 타입 등 스펙 기준 정가(list price) 추정치입니다. 비용을 언급할 때는
  항상 "추정치"임을 밝히고, 실제 청구액과 다를 수 있다고 안내하세요.
- 이 데이터에 없는 사실을 지어내지 마세요 — 모르면 모른다고 답하세요.
- 당신은 실제로 리소스를 생성·삭제·변경할 수 없습니다. 그런 요청에는 프로비저닝/인벤토리 화면을
  안내하세요.
- 한국어로, 간결하고 실행 가능한 조언 위주로 답하세요.

현재 계정 현황:
{context}
"""


class AgentNotConfiguredError(Exception):
    """OPENAI_API_KEY가 설정되지 않았을 때."""


class AgentUpstreamError(Exception):
    """OpenAI API 호출 자체가 실패했을 때(네트워크/4xx/5xx)."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body[:500]
        super().__init__(f"openai upstream error {status_code}")


def build_user_context(db: Session, user_id: int) -> str:
    """현재 사용자의 리소스/비용/최근 프로비저닝 현황을 LLM 프롬프트용 텍스트로 요약한다.

    `/resources`, `/resources/summary`, `/provisioning/jobs`가 이미 쓰는 것과 같은 쿼리 모양이다 —
    HTTP로 자기 자신을 호출하는 대신 같은 DB 세션으로 직접 조회한다.
    """
    rows = (
        db.query(Resource, CloudAccount, ServiceCatalog)
        .join(CloudAccount, Resource.cloud_account_id == CloudAccount.id)
        .join(ServiceCatalog, Resource.service_catalog_id == ServiceCatalog.id)
        .filter(
            CloudAccount.user_id == user_id,
            Resource.is_stale.is_(False),
            Resource.deleted_at.is_(None),
        )
        .all()
    )

    by_provider: dict[str, int] = {}
    cost_by_provider: dict[str, Decimal] = {}
    total_cost = Decimal("0")
    resource_lines: list[str] = []

    for resource, account, service in rows:
        by_provider[account.provider] = by_provider.get(account.provider, 0) + 1
        if resource.estimated_monthly_cost is not None:
            cost_by_provider[account.provider] = (
                cost_by_provider.get(account.provider, Decimal("0")) + resource.estimated_monthly_cost
            )
            total_cost += resource.estimated_monthly_cost
        cost_text = f"${resource.estimated_monthly_cost}/mo(추정)" if resource.estimated_monthly_cost is not None else "추정불가"
        resource_lines.append(
            f"- [{account.provider}] {service.display_name}({service.category}) "
            f"'{resource.name or resource.external_resource_id}' region={resource.region or '-'} "
            f"status={resource.status or '-'} 예상비용={cost_text}"
        )

    recent_jobs = (
        db.query(ProvisioningJob, ServiceCatalog)
        .join(ServiceCatalog, ProvisioningJob.service_catalog_id == ServiceCatalog.id)
        .filter(ProvisioningJob.user_id == user_id)
        .order_by(ProvisioningJob.created_at.desc())
        .limit(_MAX_JOB_LINES)
        .all()
    )
    job_lines = [
        f"- {job.created_at.isoformat()} {service.provider}/{service.service_code} status={job.status}"
        for job, service in recent_jobs
    ]

    parts = [
        f"전체 리소스 수: {len(rows)}개",
        "클라우드별 리소스 수: " + (", ".join(f"{p} {c}개" for p, c in sorted(by_provider.items())) or "없음"),
        (
            f"정가 기반 예상 총 비용(월): ${total_cost:.2f} (추정치)"
            if total_cost > 0
            else "정가 기반 예상 비용: 추정 가능한 리소스가 없습니다(사용량 기반 서비스만 있거나 리소스 없음)."
        ),
        "클라우드별 예상 비용(월, 추정치): "
        + (", ".join(f"{p} ${c:.2f}" for p, c in sorted(cost_by_provider.items())) or "없음"),
        "리소스 목록(최대 " + str(_MAX_RESOURCE_LINES) + "개):",
        *(resource_lines[:_MAX_RESOURCE_LINES] or ["(없음)"]),
        "최근 프로비저닝 활동:",
        *(job_lines or ["(없음)"]),
    ]
    return "\n".join(parts)


async def ask_agent(message: str, history: list[dict[str, str]], context: str) -> str:
    """조립된 컨텍스트를 시스템 프롬프트로 넣고 OpenAI Chat Completions API를 호출해 답변 텍스트를
    반환한다. Anthropic과 달리 system 프롬프트도 messages 배열 안의 role="system" 항목이다."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise AgentNotConfiguredError()

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT_TEMPLATE.format(context=context)},
        *history,
        {"role": "user", "content": message},
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                _OPENAI_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "max_tokens": _MAX_TOKENS,
                    "messages": messages,
                },
            )
        except httpx.HTTPError as exc:
            raise AgentUpstreamError(0, str(exc)) from exc

    if resp.status_code != 200:
        raise AgentUpstreamError(resp.status_code, resp.text)

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content", "") or ""
