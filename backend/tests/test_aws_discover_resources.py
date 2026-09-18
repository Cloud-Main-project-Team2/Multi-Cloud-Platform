"""app/providers/aws.py의 discover_resources 단위 테스트 — CloudFront 추가(2026-09-18).

실제 AWS를 부르지 않고 `aws._client`를 monkeypatch해 서비스별 가짜 클라이언트를 돌려준다.
핵심: CloudFront 배포도 조회하고, service_code·external_resource_id(=배포 Id)가 프로비저닝이
저장하는 값(routers/provisioning.py의 `_resource_attrs`)과 맞아떨어지게 한다 — 안 그러면 방금 만든
배포가 첫 동기화에 stale로 찍혀 인벤토리에서 사라진다.
"""

from __future__ import annotations

import app.providers.aws as aws


class _Paginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return iter(self._pages)


class _FakeEC2:
    def get_paginator(self, op):
        if op == "describe_instances":
            return _Paginator([{"Reservations": []}])
        if op == "describe_volumes":
            return _Paginator([{"Volumes": []}])
        raise AssertionError(op)


class _FakeRDS:
    def get_paginator(self, op):
        return _Paginator([{"DBInstances": []}])


class _FakeS3:
    def list_buckets(self):
        return {"Buckets": []}


class _FakeCloudFront:
    def __init__(self, distributions):
        self._distributions = distributions

    def get_paginator(self, op):
        assert op == "list_distributions"
        return _Paginator([{"DistributionList": {"Items": self._distributions}}])


def _patch(monkeypatch, distributions):
    def _fake_client(secret_payload, service, region):
        if service == "ec2":
            return _FakeEC2()
        if service == "rds":
            return _FakeRDS()
        if service == "s3":
            return _FakeS3()
        if service == "cloudfront":
            return _FakeCloudFront(distributions)
        raise AssertionError(service)

    monkeypatch.setattr(aws, "_client", _fake_client)


def test_discovers_cloudfront_distributions(monkeypatch):
    _patch(monkeypatch, [
        {"Id": "E1234567890", "DomainName": "d111.cloudfront.net", "Comment": "my-site", "Status": "Deployed"},
        {"Id": "EABCDEF0000", "DomainName": "d222.cloudfront.net", "Comment": "", "Status": "InProgress"},
    ])

    result = aws.discover_resources({"access_key_id": "AKIA", "secret_access_key": "s"})
    by_key = {(r.service_code, r.external_resource_id): r for r in result}

    # 프로비저닝 `_resource_attrs`가 distribution_id를 external_resource_id로 저장 — 같은 key여야 한다.
    assert ("cloudfront", "E1234567890") in by_key
    assert ("cloudfront", "EABCDEF0000") in by_key

    d1 = by_key[("cloudfront", "E1234567890")]
    assert d1.original_resource_type == "CloudFront Distribution"
    assert d1.region is None  # 전역 서비스
    assert d1.status == "DEPLOYED"
    assert d1.name == "my-site"  # Comment를 표시 이름으로

    # Comment가 비면 도메인으로 대체한다.
    assert by_key[("cloudfront", "EABCDEF0000")].name == "d222.cloudfront.net"
    assert by_key[("cloudfront", "EABCDEF0000")].status == "INPROGRESS"


def test_provisioning_key_matches_discovery_for_cloudfront():
    from app.routers.provisioning import _resource_attrs

    external_id, resource_type, region, _name = _resource_attrs(
        "aws", "cloudfront", {"distribution_id": "E1234567890"}, {"name": "my-site"}, {}
    )
    assert external_id == "E1234567890"
    assert resource_type == "CloudFront Distribution"
    assert region is None
    assert f"aws:cloudfront:{external_id}" == "aws:cloudfront:E1234567890"


def test_no_cloudfront_distributions_is_empty(monkeypatch):
    _patch(monkeypatch, [])
    result = aws.discover_resources({"access_key_id": "AKIA", "secret_access_key": "s"})
    assert [r for r in result if r.service_code == "cloudfront"] == []
