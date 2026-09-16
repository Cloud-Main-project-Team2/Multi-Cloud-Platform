"""동기 오류 응답 envelope에 자연어 설명(explanation)이 실리는지 검증(3번 작업).

세 핸들러(ApiError / RequestValidationError / 예상치 못한 예외)가 모두 `_error_body`를 거치므로
대표 경로로 확인한다.
"""

from __future__ import annotations

from app.error_catalog import explain

_EXPLANATION_FIELDS = {"symptom", "cause", "remedy", "category"}


def test_api_error_envelope_includes_explanation(client):
    # 무인증 요청 → 401 AUTHENTICATION_REQUIRED (ApiError 경로).
    resp = client.post("/api/v1/provisioning/aws/ec2", json={})
    assert resp.status_code == 401
    error = resp.json()["error"]
    assert error["code"] == "AUTHENTICATION_REQUIRED"
    assert "request_id" in error
    assert set(error["explanation"]) == _EXPLANATION_FIELDS
    # 카탈로그와 동일한 문구인지 확인(단일 소스).
    exp = explain("AUTHENTICATION_REQUIRED")
    assert error["explanation"]["remedy"] == exp.remedy
    assert error["explanation"]["category"] == "auth"


def test_api_error_404_has_explanation(client, make_user, auth_header):
    user = make_user()
    resp = client.get("/api/v1/provisioning/jobs/999999", headers=auth_header(user))
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "PROVISIONING_JOB_NOT_FOUND"
    assert error["explanation"]["symptom"]
    assert error["explanation"]["remedy"]


def test_validation_error_envelope_has_explanation_and_details(client, make_user, auth_header):
    user = make_user()
    # 확인 헤더까지 통과시킨 뒤 본문이 스키마에 안 맞아 422 검증 오류를 유도(428/400보다 뒤 단계).
    resp = client.post(
        "/api/v1/provisioning/aws/ec2",
        headers={
            **auth_header(user),
            "Idempotency-Key": "k-1",
            "X-Action-Confirmed": "true",
        },
        json={"unexpected": "shape"},
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert set(error["explanation"]) == _EXPLANATION_FIELDS
    assert error["explanation"]["category"] == "common"
    assert "details" in error
