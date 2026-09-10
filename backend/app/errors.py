"""공통 API 오류 표현.

docs/01_API_명세서_v1.1.md 2.8절의 오류 응답 envelope
(`{"error": {"code", "message", "request_id", "details"}}`)을 모든 라우터가
동일하게 만들도록 하는 공용 예외와 FastAPI exception handler.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """의도적으로 클라이언트에 보여줄 오류. 내부 예외 메시지를 그대로 담지 않는다."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(f"{code}: {message}")


def new_request_id() -> str:
    return uuid.uuid4().hex


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        body: dict = {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": new_request_id(),
            }
        }
        if exc.details:
            body["error"]["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content=body)
