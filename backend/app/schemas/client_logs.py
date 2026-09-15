"""프론트엔드 오류 리포트 수집 스키마.

**에러만 받는다** — 사용자 행동 추적은 수집하지 않는다(전송량·PII 검토 부담). 모든 필드에
길이 상한을 두는 이유는 브라우저가 보내는 값이라 신뢰할 수 없기 때문이다(로그 폭탄 방지).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# js_error: window.onerror / unhandled_rejection: Promise rejection / api_error: 실패한 API 응답
ClientLogKind = Literal["js_error", "unhandled_rejection", "api_error"]


class ClientLogEntry(BaseModel):
    kind: ClientLogKind
    message: str = Field(max_length=500)
    page_url: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=300)  # file:line:col
    stack: str | None = Field(default=None, max_length=4000)
    # api_error일 때: 실패한 요청의 경로·상태코드·서버가 돌려준 X-Request-Id(양쪽 로그를 잇는 열쇠)
    api_path: str | None = Field(default=None, max_length=300)
    api_status: int | None = None
    api_error_code: str | None = Field(default=None, max_length=100)
    server_request_id: str | None = Field(default=None, max_length=100)
    occurred_at: str | None = Field(default=None, max_length=40)


class ClientLogBatch(BaseModel):
    # 한 번에 10건까지 — 무한 루프에 빠진 페이지가 로그를 채우지 못하게 한다.
    entries: list[ClientLogEntry] = Field(min_length=1, max_length=10)
