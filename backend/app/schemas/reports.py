"""보고서 "정기 발송" 설정 조회/저장 스키마(2026-09-19).

지금까지 이 설정은 브라우저 localStorage에만 있었다 — 백엔드가 전혀 모르니 스케줄러가
"누구에게 언제 보낼지" 판단할 방법이 없었다. `GET`/`PUT /reports/settings`가 이 값을 실제로
저장한다(app/models.py의 ReportDeliverySetting, 사용자당 1행).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


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
