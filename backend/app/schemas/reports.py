"""보고서 "정기 발송" 설정 조회/저장 스키마(2026-09-19).

지금까지 이 설정은 브라우저 localStorage에만 있었다 — 백엔드가 전혀 모르니 스케줄러가
"누구에게 언제 보낼지" 판단할 방법이 없었다. `GET`/`PUT /reports/settings`가 이 값을 실제로
저장한다(app/models.py의 ReportDeliverySetting, 사용자당 1행).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

_PROVIDER_CHOICES = {"aws", "azure", "gcp"}


class ReportSettingsIn(BaseModel):
    delivery_method: str = Field(pattern="^(WEB|EMAIL)$")
    email: str | None = None
    period_type: str = Field(pattern="^(DAILY|WEEKLY|MONTHLY|HALF_YEARLY)$")

    @model_validator(mode="after")
    def _email_required_when_email_delivery(self) -> "ReportSettingsIn":
        if self.delivery_method == "EMAIL" and not (self.email and self.email.strip()):
            raise ValueError("메일 전송을 선택하면 수신 주소가 필요합니다.")
        return self


class ReportSettingsOut(BaseModel):
    delivery_method: str
    email: str | None
    period_type: str
    last_sent_at: str | None
    # 스케줄러(app/report_scheduler.py)가 매일 도는 시각을 한국시간으로 미리 환산해서
    # 내려준다 — "저장하면 바로 온다"고 오해하기 쉬워서(2026-09-19 실사용 중 확인) 화면에
    # 안내 문구로 보여준다.
    send_hour_kst: int


class ReportSettingsResponse(BaseModel):
    data: ReportSettingsOut


class ReportGenerationCreate(BaseModel):
    """"생성하기" 버튼을 누른 시점을 기록한다(2026-09-19) — 보고서 본문은 여전히 프론트가
    구성하고, 여기엔 "언제 무슨 조건으로 생성했는가"만 남는다. app/models.py::ReportGeneration
    참고."""

    period_type: str = Field(pattern="^(DAILY|WEEKLY|MONTHLY|HALF_YEARLY)$")
    period_from: str  # "YYYY-MM-DD"
    period_to: str  # "YYYY-MM-DD"
    clouds: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _clouds_known_and_from_before_to(self) -> "ReportGenerationCreate":
        unknown = set(self.clouds) - _PROVIDER_CHOICES
        if unknown:
            raise ValueError(f"알 수 없는 클라우드: {', '.join(sorted(unknown))}")
        if self.period_from > self.period_to:
            raise ValueError("period_from은 period_to보다 이후일 수 없습니다.")
        return self


class ReportGenerationOut(BaseModel):
    id: str
    period_type: str
    period_from: str
    period_to: str
    clouds: list[str]
    generated_at: str
    created_at: str
    # app/report_cost.py::build_cost_snapshot()의 결과 — 생성 시점에 고정, 조회 시 재계산하지
    # 않는다(비용 파트 요구사항, app/models.py::ReportGeneration 참고). 아직 계산에 실패한
    # 레코드(마이그레이션 직후 옛 행 등)는 None — 프론트가 "비용 정보 없음"으로 표시한다.
    cost_snapshot: dict[str, Any] | None = None


class ReportGenerationResponse(BaseModel):
    data: ReportGenerationOut


class ReportGenerationListData(BaseModel):
    items: list[ReportGenerationOut]


class ReportGenerationListResponse(BaseModel):
    data: ReportGenerationListData


class ReportGenerationDeleteData(BaseModel):
    id: str
    deleted: bool


class ReportGenerationDeleteResponse(BaseModel):
    data: ReportGenerationDeleteData
