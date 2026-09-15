"""`app/agent.py` 단위 검증 — 컨텍스트 조립 + Anthropic 호출(httpx는 monkeypatch).

이 프로젝트는 비동기 테스트 인프라(anyio_backend 등)를 따로 구성해두지 않았다 — `ask_agent()`
자체는 async def지만, 테스트는 `asyncio.run()`으로 동기 테스트 함수 안에서 그냥 실행한다
(다른 테스트 파일들과 같은 평범한 pytest 스타일을 유지하기 위해 — 새 pytest 플러그인 설정을
추가하지 않는다)."""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

import httpx
import pytest

from app.agent import AgentNotConfiguredError, AgentUpstreamError, ask_agent, build_user_context
from app.models import CloudAccount, Credential, ProvisioningJob, Resource, ServiceCatalog
from app.security.credential_crypto import encrypt_credential_json


def _make_service(db_session, provider, service_code, category="compute"):
    row = ServiceCatalog(provider=provider, service_code=service_code, category=category, display_name=service_code)
    db_session.add(row)
    db_session.flush()
    return row


def _make_account(db_session, user_id, provider):
    row = CloudAccount(user_id=user_id, provider=provider, external_account_id="acct-1")
    db_session.add(row)
    db_session.flush()
    return row


def _make_credential(db_session, account):
    ciphertext, nonce = encrypt_credential_json({"fake": "secret"})
    row = Credential(
        cloud_account_id=account.id, name="cred", encrypted_payload=ciphertext, encryption_nonce=nonce,
        encryption_key_version="v1", verified=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_build_user_context_summarizes_resources_and_costs(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user.id, "aws")
    service = _make_service(db_session, "aws", "ec2")
    now = dt.datetime.now(dt.timezone.utc)
    db_session.add(
        Resource(
            cloud_account_id=account.id,
            service_catalog_id=service.id,
            provider_resource_key="aws:ec2:i-123",
            external_resource_id="i-123",
            original_resource_type="AWS::EC2::Instance",
            name="mcp-web-01",
            region="ap-northeast-2",
            status="RUNNING",
            estimated_monthly_cost=Decimal("7.59"),
            cost_currency="USD",
            cost_source="list_price_estimate",
            cost_as_of=now,
            tags={},
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    context = build_user_context(db_session, user.id)

    assert "전체 리소스 수: 1개" in context
    assert "aws 1개" in context
    assert "$7.59" in context
    assert "mcp-web-01" in context
    assert "추정치" in context


def test_build_user_context_handles_no_resources(db_session, make_user):
    user = make_user()
    context = build_user_context(db_session, user.id)
    assert "전체 리소스 수: 0개" in context
    assert "추정 가능한 리소스가 없습니다" in context


def test_build_user_context_excludes_stale_and_deleted(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user.id, "aws")
    service = _make_service(db_session, "aws", "s3", category="storage_object")
    now = dt.datetime.now(dt.timezone.utc)
    db_session.add(
        Resource(
            cloud_account_id=account.id, service_catalog_id=service.id,
            provider_resource_key="aws:s3:stale-bucket", external_resource_id="stale-bucket",
            original_resource_type="S3 Bucket", name="stale-bucket", status="AVAILABLE",
            tags={}, first_seen_at=now, last_seen_at=now, is_stale=True,
        )
    )
    db_session.add(
        Resource(
            cloud_account_id=account.id, service_catalog_id=service.id,
            provider_resource_key="aws:s3:deleted-bucket", external_resource_id="deleted-bucket",
            original_resource_type="S3 Bucket", name="deleted-bucket", status="DELETED",
            tags={}, first_seen_at=now, last_seen_at=now, deleted_at=now,
        )
    )
    db_session.commit()

    context = build_user_context(db_session, user.id)
    assert "전체 리소스 수: 0개" in context


def test_build_user_context_includes_recent_jobs(db_session, make_user):
    user = make_user()
    account = _make_account(db_session, user.id, "gcp")
    credential = _make_credential(db_session, account)
    service = _make_service(db_session, "gcp", "compute_engine")
    job = ProvisioningJob(
        user_id=user.id, credential_id=credential.id, service_catalog_id=service.id,
        workspace_name="user-1-job-1", idempotency_key="k1", spec_json={}, status="success",
    )
    db_session.add(job)
    db_session.commit()

    context = build_user_context(db_session, user.id)
    assert "gcp/compute_engine status=success" in context


def test_ask_agent_raises_when_not_configured(monkeypatch):
    from app import agent as agent_module

    monkeypatch.setattr(agent_module, "get_settings", lambda: type("S", (), {"openai_api_key": ""})())
    with pytest.raises(AgentNotConfiguredError):
        asyncio.run(ask_agent("안녕", [], "컨텍스트"))


def test_ask_agent_returns_message_content(monkeypatch):
    from app import agent as agent_module

    monkeypatch.setattr(
        agent_module, "get_settings",
        lambda: type("S", (), {"openai_api_key": "sk-test", "openai_model": "gpt-4o-mini"})(),
    )

    async def fake_post(self, *args, **kwargs):
        request = httpx.Request("POST", agent_module._OPENAI_API_URL)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "안녕하세요, 추정치 기준으로..."}}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    reply = asyncio.run(ask_agent("안녕", [], "컨텍스트"))
    assert reply == "안녕하세요, 추정치 기준으로..."


def test_ask_agent_raises_upstream_error_on_non_200(monkeypatch):
    from app import agent as agent_module

    monkeypatch.setattr(
        agent_module, "get_settings",
        lambda: type("S", (), {"openai_api_key": "sk-test", "openai_model": "gpt-4o-mini"})(),
    )

    async def fake_post(self, *args, **kwargs):
        request = httpx.Request("POST", agent_module._OPENAI_API_URL)
        return httpx.Response(401, text="invalid api key", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(AgentUpstreamError) as exc_info:
        asyncio.run(ask_agent("안녕", [], "컨텍스트"))
    assert exc_info.value.status_code == 401
