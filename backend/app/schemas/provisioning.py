"""POST /provisioning/{provider}/{service} 요청 body.

common_spec/provider_spec의 provider별 필드 전체 목록은 아직 정책 확정 전이라
(docs/01_API_명세서_v1.1.md 1.2절) 여기서는 최소 계약만 강제하고, provider·service별
세부 검증은 각 실행기(app/services/provisioning/*)가 맡는다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProvisioningCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: int
    common_spec: dict = Field(default_factory=dict)
    provider_spec: dict = Field(default_factory=dict)
