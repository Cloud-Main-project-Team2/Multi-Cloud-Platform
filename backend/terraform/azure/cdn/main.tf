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
# Front Door는 결과 FQDN에 항상 의사난수 서브도메인을 붙여 반환한다(예:
# myendpoint-abcd1234.z01.azurefd.net, 서브도메인 탈취 방지 목적) — 다만 이게 "endpoint 이름
# 자체의 유일성까지 면제해준다"는 뜻은 아니다(2026-09-18 실측 정정: 이전 주석이 틀렸음). 같은
# common_spec.name으로 재시도하면 "That resource name isn't available." Conflict가 나서,
# app/azure_cdn_provisioning.py가 이름에 job_id를 붙여 매 job마다 새 이름을 쓰게 했다(S3/Storage
# Account와 같은 원칙).

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
    # route가 origin을 "enabled"로 인식하기까지 Azure 쪽 내부 전파 지연이 있어(아래 time_sleep
    # 참고), 실제 리소스는 안 만들고 대기 용도로만 쓴다.
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
  }
}

provider "azurerm" {
  features {}
}

# var.use_existing_resource_group가 true면 var.resource_group_name을 "새로 만들 이름"이 아니라
# "조회할 기존 이름"으로 쓴다(2026-09-17 결정) — 이전엔 이 이름으로 무조건 새로 만들려고 해서
# 기존 이름과 겹치면 apply가 그냥 실패했다(app/azure_cdn_provisioning.py 모듈 주석 참고).
resource "azurerm_resource_group" "this" {
  count    = var.use_existing_resource_group ? 0 : 1
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

data "azurerm_resource_group" "existing" {
  count = var.use_existing_resource_group ? 1 : 0
  name  = var.resource_group_name
}

locals {
  resource_group_name = var.use_existing_resource_group ? data.azurerm_resource_group.existing[0].name : azurerm_resource_group.this[0].name
}

resource "azurerm_cdn_frontdoor_profile" "this" {
  name                = var.profile_name
  resource_group_name = local.resource_group_name
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
  # 명시적으로 안 주면 Azure가 이 origin을 비활성 상태로 만드는 것으로 보인다(2026-09-18 실측 —
  # time_sleep을 180초까지 늘려도 route 생성이 "at least one enabled origin" 에러로 계속 실패했다.
  # `enabled` 필드가 schema상 optional+computed라 "안 주면 알아서 켜지겠지"로 가정한 게 틀렸다).
  enabled = true
}

# 원래 "180초를 줘도 계속 실패"했던 진짜 원인은 타이밍이 아니라 위 azurerm_cdn_frontdoor_origin에
# `enabled`를 안 줘서였다(2026-09-18 실측 정정 — schema가 optional+computed라 "안 주면 켜지겠지"로
# 가정한 게 틀렸음, 바로 위 주석 참고). `enabled=true`를 명시한 뒤로는 이 대기가 필요 없을 가능성이
# 높지만, Front Door 리소스 상태 전파에 약간의 지연이 있다는 커뮤니티 보고가 있어 짧게 방어적으로
# 남겨둔다.
resource "time_sleep" "wait_for_origin" {
  create_duration = "20s"
  depends_on      = [azurerm_cdn_frontdoor_origin.this]
}

resource "azurerm_cdn_frontdoor_route" "this" {
  name                          = var.route_name
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.this.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.this.id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.this.id]
  depends_on                    = [time_sleep.wait_for_origin]

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
