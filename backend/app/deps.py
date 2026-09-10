from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError, auth_required, invalid_token
from app.models import User
from app.security.jwt_tokens import TokenError, decode_user_id


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise auth_required()

    token = authorization.split(" ", 1)[1].strip()
    try:
        user_id = decode_user_id(token)
    except TokenError as exc:
        raise invalid_token() from exc

    user = db.get(User, user_id)
    if user is None:
        raise invalid_token()
    if user.status == "withdrawn":
        raise ApiError(401, "USER_WITHDRAWN", "탈퇴한 계정입니다.")

    request.state.user_id = user.id
    return user


def require_confirmation(
    x_action_confirmed: str | None = Header(default=None, alias="X-Action-Confirmed"),
) -> None:
    if x_action_confirmed != "true":
        raise ApiError(428, "CONFIRMATION_REQUIRED", "이 작업은 확인이 필요합니다.")
