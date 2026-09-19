"""AI 비용 어시스턴트 API — `POST /agent/chat`.

`docs/design/확장기능/agent1.png`/`agent2.png` 목업의 채팅 인터페이스를 실제로 동작하게 만든다.
비용 데이터는 아직 정가 기반 추정치(`app/pricing.py`)뿐이라, 시스템 프롬프트가 그 한계를 항상
밝히도록 강제한다(`app/agent.py` 참고). 아직 §문서에 정식으로 편입된 절은 아니다(확장 기능) —
`AGENT_NOT_CONFIGURED`/`AGENT_UPSTREAM_ERROR`는 이 라우터 전용으로 새로 만든 오류 code다.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import AgentNotConfiguredError, AgentUpstreamError, ask_agent, build_user_context
from app.db import get_db
from app.deps import get_current_user
from app.errors import ApiError
from app.models import User

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_MAX_HISTORY = 20


class AgentMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class AgentConditions(BaseModel):
    """비용 화면이 선택한 조건(PR 8, 선택 필드). 금액은 받지 않는다 — 서버가 소유권을 확인하고 계산한다.
    없으면 당월·전체 계정·전체 팀. period_end는 /costs/*와 같은 제외 경계다."""

    period_start: dt.date | None = None
    period_end: dt.date | None = None
    provider: list[str] = Field(default_factory=list, max_length=3)
    cloud_account_id: list[str] = Field(default_factory=list, max_length=50)
    team_id: list[str] = Field(default_factory=list, max_length=50)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # 클라이언트가 이전 대화를 그대로 돌려보낸다 — 서버는 대화 기록을 저장하지 않는다(무상태).
    history: list[AgentMessage] = Field(default_factory=list, max_length=_MAX_HISTORY)
    conditions: AgentConditions | None = None  # 기존 호출과 호환 — 없으면 이전과 같은 동작


class AgentChatData(BaseModel):
    reply: str


class AgentChatResponse(BaseModel):
    data: AgentChatData


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(
    payload: AgentChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentChatResponse:
    cost_conditions = None
    if payload.conditions is not None:
        c = payload.conditions
        try:
            account_ids = [int(v) for v in c.cloud_account_id]
        except ValueError as exc:
            raise ApiError(422, "VALIDATION_ERROR", "cloud_account_id는 숫자 ID여야 합니다.", details=[{"field": "conditions.cloud_account_id", "reason": "invalid"}]) from exc
        cost_conditions = {
            "period_start": c.period_start, "period_end": c.period_end, "providers": c.provider,
            "cloud_account_ids": account_ids, "team_ids": c.team_id,
        }
    context = build_user_context(db, current_user.id, cost_conditions)
    history = [{"role": m.role, "content": m.content} for m in payload.history]

    try:
        reply = await ask_agent(payload.message, history, context)
    except AgentNotConfiguredError as exc:
        raise ApiError(
            503, "AGENT_NOT_CONFIGURED", "AI 에이전트가 아직 설정되지 않았습니다(관리자에게 API 키 설정을 요청하세요)."
        ) from exc
    except AgentUpstreamError as exc:
        raise ApiError(502, "AGENT_UPSTREAM_ERROR", "AI 응답을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc

    return AgentChatResponse(data=AgentChatData(reply=reply))
