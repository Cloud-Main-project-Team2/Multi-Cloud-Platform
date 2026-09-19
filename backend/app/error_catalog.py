"""에러 코드 → 자연어 설명(증상·원인·해결책) 중앙 카탈로그.

지금까지 에러 코드는 라우터/도메인 예외에 문자열 리터럴로 흩어져 있었고, 코드가 무엇을 뜻하는지
설명하는 단일 소스가 없었다. 이 모듈이 그 registry 역할을 한다 — 실행 작업 3종(프로비저닝·동기화·
리소스 액션)과 그 경로에서 함께 나오는 공통 코드를 다룬다.

문구 초안 근거: `docs/Error_Catalog_Draft_2026-09-16.md` §1.

소비 지점(별도 작업에서 연결):
- `app.main`의 `ApiError` 핸들러 → 응답 envelope의 `explanation`
- 프로비저닝 잡 상세 / 동기화 아이템 serializer의 `error` 객체
- 리소스 액션 결과의 `error`

구체적인 실패 원문(terraform/CSP stderr)을 친절한 한글로 승격하는 것은 `app.error_patterns`가
담당한다 — 이 모듈은 코드별 고정 설명만 제공한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorExplanation:
    """한 에러 코드에 대한 자연어 설명."""

    code: str
    symptom: str  # 무슨 일이 일어났나 (사용자가 겪는 현상)
    cause: str  # 왜 일어났나
    remedy: str  # 어떻게 해결하나
    category: str  # provisioning | sync | resource | credential | auth | cost | common | unknown

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "symptom": self.symptom,
            "cause": self.cause,
            "remedy": self.remedy,
            "category": self.category,
        }


def _e(code: str, category: str, symptom: str, cause: str, remedy: str) -> ErrorExplanation:
    return ErrorExplanation(
        code=code, category=category, symptom=symptom, cause=cause, remedy=remedy
    )


_CATALOG: dict[str, ErrorExplanation] = {
    entry.code: entry
    for entry in (
        # ---- 프로비저닝 ----
        _e(
            "IDEMPOTENCY_KEY_REQUIRED", "provisioning",
            "생성 요청이 시작도 못 하고 거부되었습니다.",
            "중복 생성을 막는 Idempotency-Key 헤더가 요청에 빠졌습니다(대개 화면 쪽 문제).",
            "페이지를 새로고침한 뒤 다시 시도하세요. 반복되면 개발팀에 문의하세요.",
        ),
        _e(
            "SERVICE_NOT_FOUND", "provisioning",
            "선택한 서비스로 생성할 수 없습니다.",
            "요청한 provider/service 조합이 서비스 카탈로그에 없습니다.",
            "지원 목록에 있는 서비스를 다시 선택하세요.",
        ),
        _e(
            "RESOURCE_NOT_PROVISIONABLE", "provisioning",
            "생성 버튼을 눌러도 진행되지 않습니다.",
            "조회 전용 등 생성 대상이 아닌 서비스입니다.",
            "생성 가능한 서비스를 선택하세요.",
        ),
        _e(
            "PROVISIONING_NOT_IMPLEMENTED", "provisioning",
            "'아직 지원하지 않음'으로 실패했습니다.",
            "해당 provider/service 조합의 실행기가 아직 구현되지 않았습니다.",
            "현재 지원되는 조합으로 생성하거나 출시를 기다려 주세요.",
        ),
        _e(
            "SECRET_FIELD_NOT_ALLOWED", "provisioning",
            "입력값 때문에 요청이 거부되었습니다.",
            "리소스 설정에 CSP 계정 비밀키로 의심되는 값이 섞여 들어갔습니다.",
            "설정 폼에는 리소스 자체 설정만 입력하세요. 계정 키는 마이페이지에서 관리합니다.",
        ),
        _e(
            "IDEMPOTENCY_KEY_REUSED", "provisioning",
            "'이미 존재하는 요청'으로 거부되었습니다.",
            "같은 Idempotency-Key로 내용이 다른 요청이 다시 전송되었습니다.",
            "새로고침 후 새로 요청하세요. 이전 작업은 작업 목록에서 확인할 수 있습니다.",
        ),
        _e(
            "PROVISIONING_JOB_NOT_FOUND", "provisioning",
            "작업 상세 조회나 취소가 되지 않습니다.",
            "잘못된 작업 ID이거나 작업이 삭제되었습니다.",
            "작업 목록에서 유효한 작업을 다시 선택하세요.",
        ),
        _e(
            "TERRAFORM_ERROR", "provisioning",
            "실제 리소스 생성 단계에서 실패했습니다.",
            "리소스 설정값이 클라우드(CSP)의 규칙에 맞지 않습니다(이름·형식 등).",
            "'상세 원인'에 표시된 값을 규칙에 맞게 고쳐 다시 시도하세요.",
        ),
        _e(
            "PROVIDER_AUTHENTICATION_FAILED", "provisioning",
            "클라우드 로그인(인증) 단계에서 실패했습니다.",
            "자격 증명이 만료·무효이거나 잘못되었습니다.",
            "마이페이지에서 자격 증명을 재검증하세요. 역할 위임이면 신뢰 정책을 점검하세요.",
        ),
        _e(
            "QUOTA_EXCEEDED", "provisioning",
            "할당량 초과로 생성에 실패했습니다.",
            "계정의 리소스 한도(vCPU·개수 등)를 초과했습니다.",
            "불필요한 리소스를 정리하거나 CSP에 한도 증설을 요청한 뒤 다시 시도하세요.",
        ),
        # ---- 동기화 ----
        _e(
            "SYNC_JOB_NOT_FOUND", "sync",
            "동기화 상태 조회나 취소가 되지 않습니다.",
            "잘못된 작업 ID이거나 작업이 삭제되었습니다.",
            "인벤토리에서 동기화를 다시 실행하세요.",
        ),
        _e(
            "JOB_ALREADY_RUNNING", "sync",
            "새 작업이 시작되지 않습니다.",
            "같은 계정에 이미 진행 중인 작업이 있습니다.",
            "진행 중인 작업이 끝난 뒤 다시 시도하세요.",
        ),
        _e(
            "CLOUD_ACCOUNT_NOT_FOUND", "sync",
            "작업이 계정 단계에서 실패했습니다.",
            "대상 클라우드 계정을 찾을 수 없습니다.",
            "마이페이지에서 계정과 자격 증명을 확인하세요.",
        ),
        # ---- 리소스 액션 ----
        _e(
            "RESOURCE_NOT_FOUND", "resource",
            "작업 대상 리소스를 찾을 수 없습니다.",
            "잘못된 리소스이거나 이미 제거되었습니다.",
            "목록을 새로고침한 뒤 다시 선택하세요.",
        ),
        _e(
            "UNSUPPORTED_OPERATION", "resource",
            "특정 동작 버튼이 동작하지 않습니다.",
            "해당 리소스 유형이 그 동작을 지원하지 않습니다.",
            "지원되는 동작만 사용하세요(예: S3 버킷은 삭제만 가능).",
        ),
        _e(
            "RESOURCE_ALREADY_DELETED", "resource",
            "작업이 거부되었습니다.",
            "이미 삭제된 리소스입니다.",
            "목록을 새로고침하세요. 추가 작업은 필요하지 않습니다.",
        ),
        _e(
            "RESOURCE_STALE", "resource",
            "작업이 사전 검사에서 막혔습니다.",
            "로컬에 저장된 정보가 오래되었습니다(마지막 동기화 이후 변경됨).",
            "동기화를 한 번 실행해 최신 상태로 맞춘 뒤 다시 시도하세요.",
        ),
        # ---- 공통(자격 증명) ----
        _e(
            "CREDENTIAL_NOT_FOUND", "credential",
            "작업이 자격 증명 단계에서 멈췄습니다.",
            "선택한 자격 증명이 없거나 provider가 일치하지 않습니다.",
            "마이페이지에서 해당 클라우드 자격 증명을 확인·등록한 뒤 다시 시도하세요.",
        ),
        _e(
            "CREDENTIAL_VERIFICATION_FAILED", "credential",
            "자격 증명 문제로 작업이 실패했습니다.",
            "등록된 키에 필수 필드가 빠졌거나 형식이 잘못되었습니다.",
            "마이페이지에서 해당 자격 증명을 '수정'으로 키를 다시 넣고 재검증하세요.",
        ),
        # ---- 비용(cost) — 2026-09-19 채택. docs/비용_개발문서/05_API계약.md §2-5의 "추가 제안" 중
        #      *_NOT_FOUND 관례를 따르는 3개만. 나머지는 CONFLICT/VALIDATION_ERROR로 흡수한다. ----
        _e(
            "TEAM_NOT_FOUND", "cost",
            "팀을 찾을 수 없습니다.",
            "잘못된 팀 ID이거나 이미 삭제된 팀입니다.",
            "비용 화면의 팀 목록을 새로고침한 뒤 다시 선택하세요.",
        ),
        _e(
            "TEAM_BUDGET_NOT_FOUND", "cost",
            "예산을 찾을 수 없습니다.",
            "잘못된 예산 ID이거나 이미 삭제된 예산입니다.",
            "예산 목록을 새로고침한 뒤 다시 선택하세요.",
        ),
        _e(
            "COST_INGESTION_RUN_NOT_FOUND", "cost",
            "비용 수집 실행을 찾을 수 없습니다.",
            "잘못된 실행 ID이거나 다른 사용자의 실행입니다.",
            "비용 화면에서 수집을 다시 실행한 뒤 그 실행 ID로 조회하세요.",
        ),
        # ---- 공통(cross-cutting) ----
        _e(
            "CLOUD_PERMISSION_DENIED", "common",
            "권한 부족으로 작업이 거부되었습니다.",
            "자격 증명에 이 작업을 수행할 권한이 없거나, 검증된 자격 증명이 없습니다.",
            "마이페이지에서 자격 증명을 검증하고 필요한 권한을 부여한 뒤 다시 시도하세요.",
        ),
        _e(
            "PROVIDER_API_ERROR", "common",
            "클라우드(CSP) 쪽 오류로 작업이 실패했습니다.",
            "CSP API 호출이 실패했습니다(일시 장애·자격 증명 무효·복호화 실패 등).",
            "잠시 후 다시 시도하고, 계속되면 request_id와 함께 개발팀에 문의하세요.",
        ),
        _e(
            "CONFIRMATION_REQUIRED", "common",
            "실행 직전에 한 번 막혔습니다.",
            "되돌리기 어려운 작업이라 재확인이 필요합니다.",
            "확인 창에서 동의한 뒤 다시 실행하세요.",
        ),
        _e(
            "JOB_NOT_CANCELLABLE", "common",
            "취소 버튼이 동작하지 않습니다.",
            "작업이 이미 성공·실패·취소로 종료되었습니다.",
            "취소할 필요가 없습니다. 결과를 작업 목록에서 확인하세요.",
        ),
        _e(
            "VALIDATION_ERROR", "common",
            "입력값이 거부되었습니다.",
            "필수 항목 누락이나 형식 위반 등 입력이 규칙에 맞지 않습니다.",
            "details가 가리키는 항목을 수정한 뒤 다시 시도하세요.",
        ),
        _e(
            "AUTHENTICATION_REQUIRED", "auth",
            "로그인 화면으로 돌아갑니다.",
            "로그인되어 있지 않거나 세션이 만료되었습니다.",
            "다시 로그인한 뒤 작업을 이어가세요.",
        ),
        _e(
            "INVALID_TOKEN", "auth",
            "요청이 인증에서 거부되었습니다.",
            "토큰이 유효하지 않거나 만료되었습니다.",
            "다시 로그인하세요.",
        ),
        _e(
            "CONFLICT", "common",
            "요청이 현재 상태와 충돌했습니다.",
            "이미 처리되었거나 동시에 다른 변경이 일어났습니다.",
            "새로고침한 뒤 다시 시도하세요.",
        ),
        _e(
            "INTERNAL_ERROR", "common",
            "원인을 알 수 없는 오류가 발생했습니다.",
            "서버 내부 오류입니다.",
            "request_id를 첨부해 개발팀에 문의하세요.",
        ),
    )
}


def is_known(code: str) -> bool:
    """카탈로그에 등록된 코드인지 여부."""
    return code in _CATALOG


def explain(code: str | None) -> ErrorExplanation:
    """코드에 대한 자연어 설명을 반환한다.

    등록되지 않은 코드(또는 None)는 category="unknown"의 일반 설명으로 폴백한다 — 소비자가
    항상 무언가를 표시할 수 있도록 None을 반환하지 않는다.
    """
    if code and code in _CATALOG:
        return _CATALOG[code]
    return ErrorExplanation(
        code=code or "UNKNOWN_ERROR",
        symptom="작업 중 오류가 발생했습니다.",
        cause="아직 설명이 준비되지 않은 오류입니다.",
        remedy="잠시 후 다시 시도하고, 계속되면 request_id와 함께 개발팀에 문의하세요.",
        category="unknown",
    )


def all_codes() -> list[str]:
    """카탈로그에 등록된 모든 코드(테스트/문서용)."""
    return list(_CATALOG)
