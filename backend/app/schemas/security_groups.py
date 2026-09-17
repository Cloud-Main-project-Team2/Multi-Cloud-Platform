"""보안그룹 관리(2026-09-17) — AWS Security Group / Azure NSG / GCP 방화벽 규칙을 프로비저닝과
별개로 생성·수정·삭제하는 화면의 스키마. `schemas/credentials.py`의 `NetworkResourcesData`(조회
전용 요약)와 달리 규칙까지 포함한 상세를 다루고 mutation도 포함한다.

3사 모양이 근본적으로 다르다(`docs/Security_Group_Management_Design_2026-09-17.md` 참고) —
GCP는 "그룹"이 없고 방화벽 규칙 자체가 최상위 객체라 별도 모델을 쓴다. 억지로 공통 필드를
맞추지 않는다(`provisioning.js`의 `buildProviderSpec`이 provider별로 완전히 다른 모양을 쓰는
것과 같은 원칙).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# --- AWS -----------------------------------------------------------------------------------


class AwsSecurityGroupRuleOut(BaseModel):
    rule_id: str
    direction: Literal["ingress", "egress"]
    protocol: str
    from_port: int | None = None
    to_port: int | None = None
    cidr: str | None = None
    description: str | None = None


class AwsSecurityGroupOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    vpc_id: str | None = None
    ingress_rules: list[AwsSecurityGroupRuleOut] = Field(default_factory=list)
    egress_rules: list[AwsSecurityGroupRuleOut] = Field(default_factory=list)


class AwsSecurityGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=255)
    vpc_id: str = Field(min_length=1)


class AwsSecurityGroupRuleCreate(BaseModel):
    direction: Literal["ingress", "egress"]
    protocol: str = Field(min_length=1)
    from_port: int | None = None
    to_port: int | None = None
    cidr: str = Field(min_length=1)
    description: str | None = None


# --- Azure -----------------------------------------------------------------------------------


class AzureSecurityRuleOut(BaseModel):
    name: str
    priority: int
    direction: Literal["Inbound", "Outbound"]
    access: Literal["Allow", "Deny"]
    protocol: str
    source_address_prefix: str | None = None
    destination_port_range: str | None = None
    description: str | None = None


class AzureNetworkSecurityGroupOut(BaseModel):
    id: str
    name: str
    resource_group: str
    location: str
    rules: list[AzureSecurityRuleOut] = Field(default_factory=list)


class AzureNetworkSecurityGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    resource_group: str = Field(min_length=1)
    location: str = Field(min_length=1)


class AzureSecurityRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    priority: int = Field(ge=100, le=4096)
    direction: Literal["Inbound", "Outbound"]
    access: Literal["Allow", "Deny"]
    protocol: str = Field(min_length=1)
    source_address_prefix: str = Field(min_length=1)
    destination_port_range: str = Field(min_length=1)
    description: str | None = None


# --- GCP -----------------------------------------------------------------------------------
# GCP는 "그룹"이 없다 — 방화벽 규칙 자체가 최상위 객체라 group/rule 구분 없이 하나의 모델로 다룬다.


class GcpFirewallRuleOut(BaseModel):
    name: str
    network: str
    direction: Literal["INGRESS", "EGRESS"]
    priority: int
    action: Literal["allow", "deny"]
    protocol: str | None = None
    ports: list[str] = Field(default_factory=list)
    source_ranges: list[str] = Field(default_factory=list)
    target_tags: list[str] = Field(default_factory=list)


class GcpFirewallRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    network: str = Field(min_length=1)
    direction: Literal["INGRESS", "EGRESS"] = "INGRESS"
    priority: int = Field(default=1000, ge=0, le=65535)
    action: Literal["allow", "deny"] = "allow"
    protocol: str = Field(min_length=1)
    ports: list[str] = Field(default_factory=list)
    source_ranges: list[str] = Field(default_factory=list)
    target_tags: list[str] = Field(default_factory=list)


# --- 공통 응답 봉투 -----------------------------------------------------------------------


class SecurityGroupOut(BaseModel):
    """provider마다 유효한 필드만 채워지고 나머지는 기본값이다(`NetworkResourcesData`와 같은
    관례). 목록/단건 조회 공통 응답 모양."""

    aws: AwsSecurityGroupOut | None = None
    azure: AzureNetworkSecurityGroupOut | None = None
    gcp: GcpFirewallRuleOut | None = None


class SecurityGroupResponse(BaseModel):
    data: SecurityGroupOut


class SecurityGroupListData(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class SecurityGroupListResponse(BaseModel):
    data: SecurityGroupListData


class SecurityGroupCreateRequest(BaseModel):
    """provider별로 필요한 필드만 채워 보낸다 — 서버가 credential의 provider를 보고 그에 맞는
    하위 모델로 검증한다. GCP는 이 요청 하나로 규칙까지 완성된 방화벽 규칙을 만든다."""

    aws: AwsSecurityGroupCreate | None = None
    azure: AzureNetworkSecurityGroupCreate | None = None
    gcp: GcpFirewallRuleCreate | None = None


class SecurityGroupRuleCreateRequest(BaseModel):
    """AWS/Azure 전용 — GCP는 규칙 자체가 그룹 생성 요청과 같아서 이 엔드포인트를 쓰지 않는다."""

    aws: AwsSecurityGroupRuleCreate | None = None
    azure: AzureSecurityRuleCreate | None = None


class SecurityGroupRuleOut(BaseModel):
    aws: AwsSecurityGroupRuleOut | None = None
    azure: AzureSecurityRuleOut | None = None


class SecurityGroupRuleResponse(BaseModel):
    data: SecurityGroupRuleOut
