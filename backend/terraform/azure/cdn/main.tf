# Azure CDN 프로비저닝 모듈 — Front Door **Standard** (classic CDN 아님).
#
# 2025-08-15부터 Azure CDN Standard from Microsoft (classic) 신규 프로필 생성이 막혔고
# (2027-09-30 API 완전 은퇴 예정, Microsoft Learn classic-cdn-retirement-faq), azurerm provider의
# classic SKU 5종(Akamai/Verizon/Microsoft/ChinaCdn 계열)도 전부 폐지됐다 — 지금 만들 수 있는
# 선택지가 Front Door뿐이다. 자세한 배경은 app/azure_cdn_provisioning.py 모듈 docstring 참고.
#
# Front Door는 항상 5개 리소스(profile/endpoint/origin_group/origin/route)가 한 세트로 필요하다
# (AWS CloudFront처럼 리소스 1개로 축약 불가 — origin_group.load_balancing 블록과 route의
# patterns_to_match/supported_protocols가 azurerm 스키마상 Required임을 실제 provider 스키마
# 조회로 확인함). RG는 job마다 독립적으로 만든다(다른 Azure 러너 3개와 동일 원칙).
#
# Front Door 엔드포인트 이름은 전역 유일 제약이 없다 — Microsoft가 항상 의사난수 서브도메인을
# 붙여 반환한다(예: myendpoint-abcd1234.z01.azurefd.net, 서브도메인 탈취 방지 목적). 그래서 이름
# 자체의 전역 유일성을 신경 쓸 필요가 없다(S3/GCS 버킷과 다른 점).

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_cdn_frontdoor_profile" "this" {
  name                = var.profile_name
  resource_group_name = azurerm_resource_group.this.name
  # Standard 고정 — app/azure_cdn_provisioning.py의 validate_spec()이 Premium을 거부한다
  # (Premium은 월 $330 vs Standard $35, Microsoft Learn 가격 비교 — 이 프로젝트가 쓰지 않는
  # WAF 관리형 규칙/Private Link 오리진 때문에 10배 가까이 비쌀 이유가 없다).
  sku_name = "Standard_AzureFrontDoor"
  tags     = var.tags
}

resource "azurerm_cdn_frontdoor_endpoint" "this" {
  name                     = var.endpoint_name
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.this.id
  tags                     = var.tags
}

resource "azurerm_cdn_frontdoor_origin_group" "this" {
  name                     = var.origin_group_name
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.this.id

  # load_balancing 블록은 azurerm 스키마상 Required(min_items=1, max_items=1)지만 하위 필드는
  # 전부 기본값이 있다 — 빈 블록 하나로 충분하다(sample_size=4/successful_samples_required=3/
  # additional_latency_in_milliseconds=50 기본값 그대로).
  load_balancing {}

  health_probe {
    path                = var.health_probe_path
    protocol            = "Https"
    interval_in_seconds = var.health_probe_interval_seconds
  }
}

resource "azurerm_cdn_frontdoor_origin" "this" {
  name                          = var.origin_name
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.this.id

  host_name = var.origin_host_name
  # origin_host_header를 안 넣으면 뷰어가 보낸 Host(예: xxx.z01.azurefd.net)가 그대로 오리진에
  # 전달돼 Blob Storage/Web Apps 같은 오리진이 그 호스트를 몰라 400/404가 난다(azurerm 공식 문서
  # 경고) — 오리진 호스트명과 항상 동일하게 고정한다.
  origin_host_header              = var.origin_host_name
  certificate_name_check_enabled  = true
  http_port                       = 80
  https_port                      = 443
}

resource "azurerm_cdn_frontdoor_route" "this" {
  name                          = var.route_name
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.this.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.this.id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.this.id]

  supported_protocols     = var.supported_protocols
  patterns_to_match       = ["/*"]
  forwarding_protocol     = var.forwarding_protocol
  https_redirect_enabled  = var.https_redirect_enabled
  link_to_default_domain  = true

  cache {
    query_string_caching_behavior = var.query_string_caching_behavior
    compression_enabled           = var.compression_enabled
    # content_types_to_compress는 Optional이고 Terraform 기본값이 없다 — compression_enabled만
    # 켜고 이 목록을 안 채우면 아무것도 압축 안 될 수 있어(azurerm 공식 문서) 고정 MIME 목록을 둔다.
    # 압축 대상은 1KB 초과 8MB 이하 파일만이다(작은 데모 파일은 압축이 안 걸린 것처럼 보일 수 있음).
    content_types_to_compress = var.content_types_to_compress
  }
}
