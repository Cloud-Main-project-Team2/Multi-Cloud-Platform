from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine
from app.errors import register_error_handlers
from app.routers.credentials import router as credentials_router
from app.routers.dev_tools import router as dev_tools_router
from app.routers.provisioning import router as provisioning_router

app = FastAPI(title="Multi-Cloud Platform API")
register_error_handlers(app)

# 개발 단계용 permissive CORS — frontend가 다른 origin/포트(nginx :8080, 로컬 file://
# 등)에서 fetch로 호출한다. 운영 배포 전 실제 프론트 도메인으로 좁혀야 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(provisioning_router)
app.include_router(credentials_router)
app.include_router(dev_tools_router)


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
