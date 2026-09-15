import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine
from app.errors import ApiError
from app.logging_config import log_access, log_business_event
from app.routers import agent, auth, client_logs, credentials, provisioning, resources, sync_jobs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 재기동 시각을 app.log에 남긴다 — 관제 중 "언제부터 로그가 끊겼나"를 컨테이너 로그 없이
    # 판단할 수 있는 기준선이다.
    log_business_event("service.started")
    yield
    log_business_event("service.stopping")


app = FastAPI(title="Multi-Cloud Platform API", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(credentials.router)
app.include_router(resources.router)
app.include_router(sync_jobs.router)
app.include_router(provisioning.router)
app.include_router(agent.router)
app.include_router(client_logs.router)

# 프론트(:8080, nginx 정적 서빙)와 API(:8000)가 서로 다른 오리진이라 브라우저 fetch에는
# CORS 허용이 필요하다. Bearer 토큰만 쓰고 쿠키는 쓰지 않으므로 allow_credentials는 False로 둔다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _client_ip(request: Request) -> str | None:
    # nginx 뒤에 서면 request.client는 프록시 주소가 된다 — 있으면 X-Forwarded-For의 첫 값을 쓴다.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    request.state.user_id = None
    started_at = time.monotonic()

    def _emit(status_code: int) -> None:
        route = request.scope.get("route")
        log_access(
            request_id=request.state.request_id,
            method=request.method,
            path=route.path if route is not None else request.url.path,
            status=status_code,
            duration_ms=round((time.monotonic() - started_at) * 1000, 2),
            user_id=request.state.user_id,
            client_ip=_client_ip(request),
        )

    try:
        response = await call_next(request)
    except Exception:
        # 처리되지 않은 예외는 이 미들웨어 **바깥**의 ServerErrorMiddleware가 500으로 바꾼다 —
        # 여기서 잡지 않으면 정작 가장 보고 싶은 요청이 access.log에 한 줄도 남지 않는다.
        # 기록만 하고 그대로 올려 기존 500 응답 경로를 바꾸지 않는다.
        _emit(500)
        raise

    _emit(response.status_code)
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
    # 의도한 오류라 스택트레이스는 남기지 않지만, access.log의 status만으로는 원인을 알 수 없어
    # error code는 남긴다. 5xx는 우리 쪽 문제이므로 ERROR로 올린다.
    log_business_event(
        "http.api_error",
        level="ERROR" if exc.status_code >= 500 else "WARNING",
        request_id=getattr(request.state, "request_id", None),
        user_id=getattr(request.state, "user_id", None),
        method=request.method,
        path=request.url.path,
        status=exc.status_code,
        error_code=exc.code,
    )
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
    # details에는 필드명과 오류 유형만 들어간다(값은 넣지 않는다 — 비밀번호 등이 섞일 수 있다).
    log_business_event(
        "http.validation_error",
        level="WARNING",
        request_id=getattr(request.state, "request_id", None),
        user_id=getattr(request.state, "user_id", None),
        method=request.method,
        path=request.url.path,
        details=details,
    )
    return JSONResponse(
        status_code=422,
        content=_error_body(request, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다.", details),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # 응답에는 내부 정보를 숨기지만(§17) 로그에는 반드시 남긴다 — 이 로그가 없으면 500의 원인을
    # 추적할 방법이 uvicorn 콘솔뿐이고, 컨테이너를 재시작하는 순간 사라진다.
    log_business_event(
        "http.unhandled_exception",
        level="ERROR",
        exc_info=True,
        request_id=getattr(request.state, "request_id", None),
        user_id=getattr(request.state, "user_id", None),
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
    )
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
