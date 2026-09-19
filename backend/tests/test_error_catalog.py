"""에러 코드 자연어 카탈로그(`app.error_catalog`) 검증."""

from __future__ import annotations

import pytest

from app.error_catalog import all_codes, explain, is_known

# 실행 3종 + 공통 경로에서 실제로 나오는 코드 — 카탈로그가 반드시 다뤄야 한다.
IN_SCOPE_CODES = [
    # 프로비저닝
    "IDEMPOTENCY_KEY_REQUIRED", "SERVICE_NOT_FOUND", "RESOURCE_NOT_PROVISIONABLE",
    "PROVISIONING_NOT_IMPLEMENTED", "SECRET_FIELD_NOT_ALLOWED", "IDEMPOTENCY_KEY_REUSED",
    "PROVISIONING_JOB_NOT_FOUND", "TERRAFORM_ERROR", "PROVIDER_AUTHENTICATION_FAILED",
    "QUOTA_EXCEEDED",
    # 동기화
    "SYNC_JOB_NOT_FOUND", "JOB_ALREADY_RUNNING", "CLOUD_ACCOUNT_NOT_FOUND",
    # 리소스 액션
    "RESOURCE_NOT_FOUND", "UNSUPPORTED_OPERATION", "RESOURCE_ALREADY_DELETED", "RESOURCE_STALE",
    # 공통
    "CREDENTIAL_NOT_FOUND", "CREDENTIAL_VERIFICATION_FAILED", "CLOUD_PERMISSION_DENIED",
    "PROVIDER_API_ERROR", "CONFIRMATION_REQUIRED", "JOB_NOT_CANCELLABLE", "VALIDATION_ERROR",
    "AUTHENTICATION_REQUIRED", "CONFLICT", "INTERNAL_ERROR",
    # 비용(2026-09-19 채택 — 05_API계약.md §2-5)
    "TEAM_NOT_FOUND", "TEAM_BUDGET_NOT_FOUND", "COST_INGESTION_RUN_NOT_FOUND",
]

_VALID_CATEGORIES = {"provisioning", "sync", "resource", "credential", "auth", "cost", "common", "unknown"}


@pytest.mark.parametrize("code", IN_SCOPE_CODES)
def test_in_scope_codes_are_registered(code):
    assert is_known(code), f"{code} 가 카탈로그에 없음"


@pytest.mark.parametrize("code", IN_SCOPE_CODES)
def test_explain_returns_complete_nonempty_fields(code):
    exp = explain(code)
    assert exp.code == code
    assert exp.symptom and exp.cause and exp.remedy, f"{code} 설명 필드가 비어 있음"
    assert exp.category in _VALID_CATEGORIES


def test_every_catalog_entry_is_wellformed():
    for code in all_codes():
        exp = explain(code)
        assert exp.symptom.strip()
        assert exp.cause.strip()
        assert exp.remedy.strip()
        assert exp.category in _VALID_CATEGORIES - {"unknown"}


def test_as_dict_shape():
    exp = explain("TERRAFORM_ERROR")
    d = exp.as_dict()
    assert set(d) == {"code", "symptom", "cause", "remedy", "category"}
    assert d["code"] == "TERRAFORM_ERROR"


def test_unknown_code_falls_back_without_raising():
    exp = explain("SOME_BRAND_NEW_CODE")
    assert exp.category == "unknown"
    assert exp.code == "SOME_BRAND_NEW_CODE"
    assert exp.symptom and exp.remedy
    assert not is_known("SOME_BRAND_NEW_CODE")


def test_none_code_falls_back():
    exp = explain(None)
    assert exp.category == "unknown"
    assert exp.code == "UNKNOWN_ERROR"
