"""개발/QA 전용 임시 엔드포인트.

⚠️ `docs/01_API_명세서_v1.1.md`에 정의된 공식 계약이 아니다. 회원가입(`POST
/auth/sign-up`)이 아직 구현되지 않아 수동 테스트용 사용자를 만들 방법이 없어서
만든 것 — 실제 회원가입이 구현되면 이 라우터는 삭제한다. `dev-tools/`의 수동
테스트 페이지에서만 쓰는 걸 전제로 하며, 비밀번호 없이 bare user row만 만든다
(`app/security/auth.py`의 임시 인증이 `Authorization: Bearer <user_id>`만
검사하므로 로그인 절차 자체가 필요 없다).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError
from app.models import User

router = APIRouter(prefix="/api/v1/dev", tags=["dev-tools"])


class DevUserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    name: str


@router.post("/users", status_code=201)
def create_dev_user(body: DevUserCreateRequest, db: Session = Depends(get_db)) -> dict:
    normalized = body.email.strip().lower()
    user = User(email=body.email, normalized_email=normalized, name=body.name, affiliation_type="individual")
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "EMAIL_ALREADY_EXISTS", "이미 등록된 이메일입니다.") from exc

    return {"data": {"id": str(user.id), "email": user.email, "name": user.name}}
