import time
import uuid

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine
from app.errors import ApiError
from app.logging_config import log_access
from app.routers import auth

app = FastAPI(title="Multi-Cloud Platform API")
app.include_router(auth.router)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    request.state.user_id = None
    started_at = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - started_at) * 1000, 2)

    route = request.scope.get("route")
    path_template = route.path if route is not None else request.url.path
    log_access(
        request_id=request.state.request_id,
        method=request.method,
        path=path_template,
        status=response.status_code,
        duration_ms=duration_ms,
        user_id=request.state.user_id,
    )
    response.headers["X-Request-Id"] = request.state.request_id
    return response


def _error_body(request: Request, code: str, message: str, details: list[dict] | None = None) -> dict:
    error: dict = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
    }
    if details:
        error["details"] = details
    return {"error": error}


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(request, exc.code, exc.message, exc.details),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in err["loc"] if p != "body"), "reason": err["type"]}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_error_body(request, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다.", details),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # 내부 예외 메시지는 절대 그대로 노출하지 않는다(§17).
    return JSONResponse(
        status_code=500,
        content=_error_body(request, "INTERNAL_ERROR", "예상하지 못한 오류가 발생했습니다."),
    )


@app.get("/health")
def health() -> dict:
    """Process liveness only — does not touch the database."""
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response) -> dict:
    """Readiness — confirms the database connection is usable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ok"}
