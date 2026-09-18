"""AWS CloudFront 삭제 지원 검증 (2026-09-18).

3사 프로비저닝 가능 서비스 중 유일하게 어떤 동작도 지원하지 않던 서비스였다(사용자 확인 —
"프로비저닝으로 만든 건 다 start/stop/delete가 돼야 한다"). CloudFront는 활성화된 배포를 바로
지울 수 없어(DistributionNotDisabled) 먼저 비활성화를 요청하고 사용자에게 재시도를 안내하는
2단계 흐름을 검증한다. `app/providers/aws.py`의 `_client()`를 monkeypatch해서 실제 boto3 호출
없이 결정적으로 확인한다(discover_resources 계열 단위 테스트와 같은 방식).
"""

from __future__ import annotations

from botocore.exceptions import ClientError

import app.providers.aws as aws_provider
from app.resource_actions import ResourceActionError, perform_action, supported_actions

_SECRET = {"access_key_id": "AKIA", "secret_access_key": "s"}


def test_cloudfront_supports_delete_only():
    assert supported_actions("aws", "cloudfront", "CloudFront Distribution") == {"delete"}


def test_unsupported_cloudfront_action_is_rejected_before_dispatch(monkeypatch):
    called = []
    monkeypatch.setattr(aws_provider, "_client", lambda *a, **k: called.append(a) or object())

    try:
        perform_action(
            provider="aws", service_code="cloudfront", original_resource_type="CloudFront Distribution",
            action="start", secret_payload=_SECRET, external_account_id="111111111111", region=None,
            external_resource_id="E123",
        )
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "UNSUPPORTED_OPERATION"
    assert called == []  # 어댑터까지 도달하지 않고 사전에 막혀야 한다


class _FakeCloudFront:
    def __init__(self, enabled: bool, delete_error_code: str | None = None):
        self.calls: list[tuple] = []
        self._enabled = enabled
        self._delete_error_code = delete_error_code

    def get_distribution(self, Id):
        self.calls.append(("get_distribution", Id))
        return {"ETag": "etag-1", "Distribution": {"DistributionConfig": {"Enabled": self._enabled}}}

    def update_distribution(self, Id, IfMatch, DistributionConfig):
        self.calls.append(("update_distribution", Id, IfMatch, DistributionConfig["Enabled"]))

    def delete_distribution(self, Id, IfMatch):
        self.calls.append(("delete_distribution", Id, IfMatch))
        if self._delete_error_code:
            raise ClientError({"Error": {"Code": self._delete_error_code}}, "DeleteDistribution")


def test_delete_enabled_distribution_disables_first_and_asks_to_retry(monkeypatch):
    fake = _FakeCloudFront(enabled=True)
    monkeypatch.setattr(aws_provider, "_client", lambda *a, **k: fake)

    try:
        aws_provider.perform_resource_action("cloudfront", "CloudFront Distribution", "delete", _SECRET, None, "E123")
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "CloudFrontNotDisabled"
        assert "비활성화" in err.message

    assert ("update_distribution", "E123", "etag-1", False) in fake.calls
    assert not any(c[0] == "delete_distribution" for c in fake.calls)  # 바로 지우려 하면 안 됨


def test_delete_already_disabled_distribution_succeeds(monkeypatch):
    fake = _FakeCloudFront(enabled=False)
    monkeypatch.setattr(aws_provider, "_client", lambda *a, **k: fake)

    aws_provider.perform_resource_action("cloudfront", "CloudFront Distribution", "delete", _SECRET, None, "E123")

    assert ("delete_distribution", "E123", "etag-1") in fake.calls
    assert not any(c[0] == "update_distribution" for c in fake.calls)


def test_delete_races_with_pending_disable_propagation(monkeypatch):
    fake = _FakeCloudFront(enabled=False, delete_error_code="DistributionNotDisabled")
    monkeypatch.setattr(aws_provider, "_client", lambda *a, **k: fake)

    try:
        aws_provider.perform_resource_action("cloudfront", "CloudFront Distribution", "delete", _SECRET, None, "E123")
        raise AssertionError("ResourceActionError가 발생해야 함")
    except ResourceActionError as err:
        assert err.code == "CloudFrontNotDisabled"
