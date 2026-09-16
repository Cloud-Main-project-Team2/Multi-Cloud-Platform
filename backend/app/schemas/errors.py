"""잡(프로비저닝/동기화) 실패 정보에 얹는 자연어 설명 스키마.

동기 오류 envelope(`app.main._error_body`)와 같은 개념을 백그라운드 잡 결과에도 실어 준다.
차이점: 잡은 실제 실패 원문(terraform/CSP stderr)이 DB에 저장돼 있어, code별 고정 설명
(`explanation`)에 더해 원문을 번역한 '구체 원인'(`specific_reason`)까지 붙일 수 있다.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.error_catalog import explain
from app.error_patterns import translate_reason


class ErrorExplanationOut(BaseModel):
    symptom: str
    cause: str
    remedy: str
    category: str


def explanation_for(code: str | None) -> ErrorExplanationOut:
    exp = explain(code)
    return ErrorExplanationOut(
        symptom=exp.symptom, cause=exp.cause, remedy=exp.remedy, category=exp.category
    )


def specific_reason_for(code: str | None, message: str | None) -> str | None:
    """저장된 실패 원문을 친절한 한글 원인으로 번역한다(매칭 없으면 None)."""
    return translate_reason(message, code=code)
