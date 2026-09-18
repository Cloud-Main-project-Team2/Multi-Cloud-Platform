"""AI 비용 어시스턴트 API — `POST /agent/chat`.

`docs/design/확장기능/agent1.png`/`agent2.png` 목업의 채팅 인터페이스를 실제로 동작하게 만든다.
비용 데이터는 아직 정가 기반 추정치(`app/pricing.py`)뿐이라, 시스템 프롬프트가 그 한계를 항상
밝히도록 강제한다(`app/agent.py` 참고). 아직 §문서에 정식으로 편입된 절은 아니다(확장 기능) —
`AGENT_NOT_CONFIGURED`/`AGENT_UPSTREAM_ERROR`는 이 라우터 전용으로 새로 만든 오류 code다.
"""

from __future__ import annotations

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


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # 클라이언트가 이전 대화를 그대로 돌려보낸다 — 서버는 대화 기록을 저장하지 않는다(무상태).
    history: list[AgentMessage] = Field(default_factory=list, max_length=_MAX_HISTORY)


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
    context = build_user_context(db, current_user.id)
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
