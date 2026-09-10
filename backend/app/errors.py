"""API 명세서 v1.1 §2.8/§17 오류 응답 규약을 코드로 옮긴 예외 타입.

라우터는 이 예외들을 raise하면 되고, 실제 JSON envelope 조립과 request_id 부착은
`app.main`의 전역 예외 핸들러가 담당한다.
"""

from __future__ import annotations


class ApiError(Exception):
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
        super().__init__(code)


def auth_required(message: str = "인증이 필요합니다.") -> ApiError:
    return ApiError(401, "AUTHENTICATION_REQUIRED", message)


def invalid_token(message: str = "유효하지 않은 토큰입니다.") -> ApiError:
    return ApiError(401, "INVALID_TOKEN", message)


def confirmation_required() -> ApiError:
    return ApiError(428, "CONFIRMATION_REQUIRED", "이 작업은 확인이 필요합니다.")


def validation_error(message: str, details: list[dict] | None = None) -> ApiError:
    return ApiError(422, "VALIDATION_ERROR", message, details)
