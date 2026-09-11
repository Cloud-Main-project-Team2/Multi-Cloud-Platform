"""여러 provider의 compute 실행기가 공유하는 `common_spec` 조각.

`docs/멀티클라우드 3사 기능 맵핑 — 설정값 입력 범위 (2026-09-10).md` 1절 기준으로
Compute(EC2/Virtual Machines/Compute Engine)의 공통 설정 7개 중, provider마다
다르게 해석해야 하는 이름·리전·사양·인증을 뺀 나머지(태그, 인바운드 규칙)는 AWS/GCP
compute 실행기가 생기면 그대로 재사용할 수 있게 여기 모아둔다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InboundRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: int = Field(ge=1, le=65535)
    cidr: str = "0.0.0.0/0"


class ComputeCommonSpec(BaseModel):
    """Compute 공통 설정. `network`/`specTier` 등 이 실행기가 아직 안 쓰는 필드는
    frontend가 계속 보내더라도 깨지지 않도록 `extra="allow"`로 통과시킨다."""

    model_config = ConfigDict(extra="allow")

    name: str
    tags: dict[str, str] = Field(default_factory=dict)
    # 프론트 기본값은 22/tcp 하나가 체크된 상태 — 빈 배열이면 인바운드를 아무것도 열지 않는다
    # (서버가 임의로 기본 규칙을 만들어 끼워넣지 않는다).
    inbound_rules: list[InboundRule] = Field(default_factory=list)
