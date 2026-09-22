from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateCredentialRequest(BaseModel):
    external_account_id: str = Field(min_length=1, max_length=255)
    account_label: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    public_identifier: str | None = Field(default=None, max_length=255)
    secret_payload: dict[str, Any]
    tags: dict[str, Any] = Field(default_factory=dict)
    display_order: int = Field(default=0, ge=0)


class PatchCloudAccountRequest(BaseModel):
    account_label: str | None = Field(default=None, max_length=200)


class PatchCredentialRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tags: dict[str, Any] | None = None
    display_order: int | None = Field(default=None, ge=0)
    public_identifier: str | None = Field(default=None, max_length=255)
    secret_payload: dict[str, Any] | None = None


class CredentialOrderItem(BaseModel):
    credential_id: str
    display_order: int = Field(ge=0)


class CredentialOrderRequest(BaseModel):
    items: list[CredentialOrderItem] = Field(min_length=1)


class CloudAccountOut(BaseModel):
    id: str
    provider: str
    external_account_id: str
    account_label: str | None
    created_at: str
    updated_at: str


class CredentialOut(BaseModel):
    id: str
    cloud_account_id: str
    name: str
    masked_public_identifier: str | None
    permission_scope: dict[str, Any]
    verified: bool
    verified_at: str | None
    tags: dict[str, Any]
    display_order: int
    created_at: str
    updated_at: str
    # 이 자격 증명이 어떤 인증 방식인지. 화면에서 "레거시 키" 배지를 띄우는 데 쓴다.
    auth_type: str = "access_key"
    # 방금 수행한 검증이 실패한 이유. **저장하지 않는다** — 등록/수정/재검증 응답에만 담기고
    # 목록 조회에서는 항상 None이다. AccessDenied는 원인이 구분되지 않아 사용자가 스스로
    # 좁힐 수 있도록 안내 문구를 함께 내려준다.
    verification_error_code: str | None = None
    verification_error_message: str | None = None


class CloudAccountResponse(BaseModel):
    data: CloudAccountOut


class CloudAccountListData(BaseModel):
    items: list[CloudAccountOut]
    total: int
    pagination: None = None


class CloudAccountListResponse(BaseModel):
    data: CloudAccountListData


class CredentialResponse(BaseModel):
    data: CredentialOut


class CredentialListData(BaseModel):
    items: list[CredentialOut]
    total: int
    pagination: None = None


class CredentialListResponse(BaseModel):
    data: CredentialListData


class VerifyResponseData(BaseModel):
    credential_id: str
    verified: bool
    verified_at: str | None
    permission_scope: dict[str, Any]
    verification_error_code: str | None = None
    verification_error_message: str | None = None


class AwsDelegationSetupData(BaseModel):
    """AWS 역할 위임 온보딩에 필요한 값. 사용자는 이걸 보고 자기 계정에 역할을 만든다."""

    platform_account_id: str
    external_id: str
    role_name_prefix: str
    suggested_role_name: str
    trust_policy: dict[str, Any]
    managed_policy_arns: list[str]
    # 인라인 정책의 Statement 배열을 그대로 준다(2026-09-17 — 예전엔 action 문자열 목록만 주고
    # 프론트가 "Resource: *" 한 statement로 조립했는데, mcp-ssm-* 리소스로 좁혀야 하는 IAM 관리
    # 권한이 추가되면서 statement가 2개로 늘어 프론트에서 조립할 수 없게 됐다 — 서버가 완성된
    # 모양을 내려주고 프론트는 그대로 렌더링만 한다).
    inline_statements: list[dict[str, Any]]
    iam_console_url: str
    troubleshooting: list[str]


class AwsDelegationSetupResponse(BaseModel):
    data: AwsDelegationSetupData


class VerifyResponse(BaseModel):
    data: VerifyResponseData


class AwsVpcOut(BaseModel):
    id: str
    cidr_block: str | None = None
    name: str | None = None
    is_default: bool = False


class AwsSubnetOut(BaseModel):
    id: str
    vpc_id: str
    availability_zone: str | None = None
    cidr_block: str | None = None
    name: str | None = None


class AwsSecurityGroupOut(BaseModel):
    id: str
    vpc_id: str | None = None
    name: str | None = None


class AzureResourceGroupOut(BaseModel):
    name: str
    location: str


class AzureVirtualNetworkOut(BaseModel):
    id: str
    name: str
    resource_group: str
    location: str
    address_space: list[str] = Field(default_factory=list)


class AzureNetworkSecurityGroupOut(BaseModel):
    id: str
    name: str
    resource_group: str
    location: str


class AzureSubnetOut(BaseModel):
    id: str
    name: str
    vnet_name: str
    resource_group: str
    address_prefix: str | None = None


class GcpNetworkOut(BaseModel):
    name: str
    self_link: str
    auto_create_subnetworks: bool = False


class GcpBucketOut(BaseModel):
    """CDN "기존 버킷 연결"이 자유 텍스트 대신 실제 목록에서 고르게 하려고 추가(2026-09-22)."""

    name: str
    location: str


class NetworkResourcesData(BaseModel):
    """프로비저닝 폼 "기존 리소스 사용"이 실제 목록을 보여줄 때 쓰는 응답(2026-09-17) — provider마다
    유효한 필드만 채워지고 나머지는 빈 리스트다."""

    vpcs: list[AwsVpcOut] = Field(default_factory=list)
    subnets: list[AwsSubnetOut] = Field(default_factory=list)
    security_groups: list[AwsSecurityGroupOut] = Field(default_factory=list)
    resource_groups: list[AzureResourceGroupOut] = Field(default_factory=list)
    virtual_networks: list[AzureVirtualNetworkOut] = Field(default_factory=list)
    network_security_groups: list[AzureNetworkSecurityGroupOut] = Field(default_factory=list)
    # AWS의 subnets(AwsSubnetOut)와 모양이 달라 이름을 분리한다.
    azure_subnets: list[AzureSubnetOut] = Field(default_factory=list)
    networks: list[GcpNetworkOut] = Field(default_factory=list)
    buckets: list[GcpBucketOut] = Field(default_factory=list)


class NetworkResourcesResponse(BaseModel):
    data: NetworkResourcesData


class VmSkuAvailabilityItem(BaseModel):
    """SKU 하나에 대한 사전 확인 결과(2026-09-18) — `status`는
    `"available" | "restricted" | "not_offered_in_region"` 중 하나다. 조회 자체가 실패한
    경우는 이 항목이 아니라 502 에러 응답이 되며(app/providers/azure.py 참고), 이 셋 중
    어느 값도 "실시간 용량까지 보장"하지는 않는다 — 사전 확인 시점의 스냅샷일 뿐이다."""

    sku: str
    status: str
    reason: str | None = None


class VmSkuAvailabilityData(BaseModel):
    region: str
    skus: list[VmSkuAvailabilityItem] = Field(default_factory=list)


class VmSkuAvailabilityResponse(BaseModel):
    data: VmSkuAvailabilityData
