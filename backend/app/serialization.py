"""Response-shaping helpers shared by routers.

API 명세서 v1.1 §2.1: ID는 bigint를 문자열로, 시각은 `...Z` 형식의 UTC ISO 8601로 반환한다.
"""

from __future__ import annotations

import datetime as dt


def iso_z(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def str_id(value: int | None) -> str | None:
    return None if value is None else str(value)


def mask_public_identifier(raw: str) -> str:
    """공개 식별자 앞 4자(+ 가능하면 끝 4자)만 남기고 나머지는 가린다.

    원본 값은 어디에도 저장하지 않는다 — 이 함수의 반환값만 DB에 쓴다.
    """
    raw = raw.strip()
    if len(raw) <= 8:
        return raw[:4] + "•" * max(len(raw) - 4, 4)
    return f"{raw[:4]}{'•' * 8}{raw[-4:]}"
