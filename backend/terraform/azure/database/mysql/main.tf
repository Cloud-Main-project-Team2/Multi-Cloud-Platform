# Azure Database for MySQL Flexible Server — 생성 전용 모듈 (비공개 전용, VNet 통합).
#
# 팀 정책(AWS RDS/S3/CloudFront와 동일): "사고 나면 되돌릴 수 없으니 기본값은 항상 안전하게" —
# 이 서비스는 실제 사용자 자격증명으로 실제 과금 리소스를 만들기 때문에, GCP Cloud SQL의
# 퍼블릭+데모 구성을 따르지 않고 AWS RDS와 같은 수준(비공개, 인터넷 노출 없음)으로 만든다.
# MySQL Flexible Server는 생성 시점에 위임된(delegated) 서브넷을 지정해야만 비공개로 만들 수
# 있고 이후 변경이 불가능하므로, VNet/서브넷/Private DNS Zone을 이 모듈이 직접 갖춘다
# (azure/vm과 같은 "job마다 자기 네트워크를 만든다" 관례).
#
# ## 기존 리소스 재사용 — 리소스 그룹/VNet까지만, 서브넷은 항상 새로 만든다 (2026-09-17 결정)
#
# var.existing_resource_group_name/existing_vnet_id가 있으면 그 RG/VNet을 그대로 쓴다(둘 다
# optional, null이면 지금까지처럼 새로 만든다). 하지만 **서브넷 자체는 항상 이 모듈이 새로
# 만든다** — 기존 서브넷을 그대로 넘겨받으면 Terraform이 그 서브넷에 MySQL Flexible Server
# 전용 위임(delegation)을 강제로 걸어야 하는데, 이미 다른 용도로 쓰이고 있는 서브넷이면 그
# 변경만으로 같은 서브넷의 다른 리소스가 예고 없이 깨질 수 있다(Azure는 위임된 서브넷에 다른
# 리소스 배치를 제한한다). 그래서 주소 공간만 기존 VNet에서 빌리고, 서브넷 자체는 이 job
# 전용으로 새로 판다 — 기존 인프라를 건드리지 않는다.
#
# 기존 VNet의 주소 공간과 겹치지 않는 서브넷 CIDR을 직접 안다면 var.existing_vnet_subnet_cidr로
# 넘길 수 있다(권장) — 안 주면 그 VNet의 첫 번째 주소 공간에서 /24를 하나 추정해 쓰는데, 이미
# 다른 서브넷이 그 대역을 쓰고 있으면 apply가 그냥 실패한다(기존 인프라를 몰래 덮어쓰지 않고
# 실패로 알린다는 원칙 그대로).

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
  count    = var.existing_resource_group_name == null ? 1 : 0
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

data "azurerm_resource_group" "existing" {
  count = var.existing_resource_group_name != null ? 1 : 0
  name  = var.existing_resource_group_name
}

locals {
  resource_group_name = var.existing_resource_group_name != null ? data.azurerm_resource_group.existing[0].name : azurerm_resource_group.this[0].name
  location             = var.existing_resource_group_name != null ? data.azurerm_resource_group.existing[0].location : azurerm_resource_group.this[0].location

  # ARM 리소스 ID는 "/subscriptions/{sub}/resourceGroups/{rg}/providers/{ns}/{type}/{name}" 형태로
  # 고정돼 있다 — split해서 리소스 그룹/이름을 꺼낸다(provider v3엔 ID 파싱 내장 함수가 없다).
  existing_vnet_id_parts    = var.existing_vnet_id != null ? split("/", var.existing_vnet_id) : null
  existing_vnet_rg_name     = var.existing_vnet_id != null ? local.existing_vnet_id_parts[4] : null
  existing_vnet_name        = var.existing_vnet_id != null ? local.existing_vnet_id_parts[8] : null
}

resource "azurerm_virtual_network" "this" {
  count               = var.existing_vnet_id == null ? 1 : 0
  name                = "${var.server_name}-vnet"
  address_space       = ["10.0.0.0/16"]
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = var.tags
}

data "azurerm_virtual_network" "existing" {
  count               = var.existing_vnet_id != null ? 1 : 0
  name                = local.existing_vnet_name
  resource_group_name  = local.existing_vnet_rg_name
}

locals {
  vnet_resource_group_name = var.existing_vnet_id != null ? local.existing_vnet_rg_name : local.resource_group_name
  vnet_name                = var.existing_vnet_id != null ? local.existing_vnet_name : azurerm_virtual_network.this[0].name
  vnet_id                  = var.existing_vnet_id != null ? var.existing_vnet_id : azurerm_virtual_network.this[0].id
  vnet_address_space       = var.existing_vnet_id != null ? data.azurerm_virtual_network.existing[0].address_space[0] : "10.0.0.0/16"
  subnet_cidr              = var.existing_vnet_subnet_cidr != null ? var.existing_vnet_subnet_cidr : cidrsubnet(local.vnet_address_space, 8, 0)
}

# MySQL Flexible Server 전용으로 위임된 서브넷 — 생성 후 변경 불가하므로 이 서브넷은 다른
# 어떤 리소스와도 공유하지 않는다(기존 VNet을 재사용해도 서브넷 자체는 항상 새로 만든다 — 위 설명 참고).
resource "azurerm_subnet" "this" {
  name                 = "${var.server_name}-subnet"
  resource_group_name  = local.vnet_resource_group_name
  virtual_network_name = local.vnet_name
  address_prefixes     = [local.subnet_cidr]

  delegation {
    name = "mysql-flexible-server-delegation"
    service_delegation {
      name    = "Microsoft.DBforMySQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# Private DNS Zone 이름은 Azure 요구사항상 "mysql.database.azure.com"으로 끝나야 한다. 이 zone은
# job 전용 리소스 그룹 안에서만 유일하면 되므로(전역 유일성 아님) 고정 이름을 쓴다.
# 서버 이름을 그대로 접두사로 쓰면 "InvalidPrivateDnsZoneName"으로 거부된다(2026-09-18 실측 —
# 서버의 실제 FQDN처럼 보이는 이름은 Azure가 막는 것으로 보인다). 고정된 일반 접두사로 바꿨다.
resource "azurerm_private_dns_zone" "this" {
  name                = "mcp.mysql.database.azure.com"
  resource_group_name = local.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  name                  = "${var.server_name}-dns-link"
  resource_group_name   = local.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.this.name
  virtual_network_id    = local.vnet_id
}

resource "azurerm_mysql_flexible_server" "this" {
  name                = var.server_name
  resource_group_name = local.resource_group_name
  location            = local.location

  administrator_login    = var.admin_login
  administrator_password = var.admin_password

  # 맵핑 문서 "사양 완전 제외" — 가장 작은 Burstable 고정. B_Standard_B1s는 실제 구독으로 테스트해보니
  # "OperationNotSupportedStandardB1s"로 거부됨(2026-09-18 실측) — PostgreSQL의 B1s 미지원과 같은
  # 계열의 제약으로 보여 PostgreSQL과 동일하게 B1ms로 통일한다.
  sku_name              = "B_Standard_B1ms"
  version               = "8.0.21"
  backup_retention_days  = 7

  storage {
    size_gb = 20 # Flexible Server 최소값
  }

  delegated_subnet_id = azurerm_subnet.this.id
  private_dns_zone_id = azurerm_private_dns_zone.this.id

  tags = var.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.this]
}

resource "azurerm_mysql_flexible_database" "this" {
  name                = var.database_name
  resource_group_name = local.resource_group_name
  server_name         = azurerm_mysql_flexible_server.this.name
  charset             = "utf8mb4"
  collation           = "utf8mb4_unicode_ci"
}
