from fastapi import FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine
from app.errors import register_error_handlers
from app.routers.provisioning import router as provisioning_router

app = FastAPI(title="Multi-Cloud Platform API")
register_error_handlers(app)
app.include_router(provisioning_router)


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
