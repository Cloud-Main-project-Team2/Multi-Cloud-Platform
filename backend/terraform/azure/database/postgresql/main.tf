# Azure Database for PostgreSQL Flexible Server — 생성 전용 모듈 (비공개 전용, VNet 통합).
#
# mysql 서브모듈과 같은 원칙(팀 정책: AWS RDS/S3/CloudFront와 동일하게 기본값은 항상 안전하게 —
# GCP Cloud SQL의 퍼블릭+데모 구성을 따르지 않는다) — 생성 시점에 위임된 서브넷을 지정해 비공개로
# 만들고 이후 변경 불가능하므로, VNet/서브넷/Private DNS Zone을 이 모듈이 직접 갖춘다.
#
# 기존 리소스 재사용은 mysql 서브모듈과 동일한 원칙(2026-09-17 결정, 그쪽 주석 참고) — 리소스
# 그룹/VNet까지만 재사용하고 서브넷은 위임 때문에 항상 새로 만든다.

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

  existing_vnet_id_parts = var.existing_vnet_id != null ? split("/", var.existing_vnet_id) : null
  existing_vnet_rg_name  = var.existing_vnet_id != null ? local.existing_vnet_id_parts[4] : null
  existing_vnet_name     = var.existing_vnet_id != null ? local.existing_vnet_id_parts[8] : null
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

# PostgreSQL Flexible Server 전용으로 위임된 서브넷 — 생성 후 변경 불가하므로 다른 리소스와
# 공유하지 않는다(기존 VNet을 재사용해도 서브넷 자체는 항상 새로 만든다 — 위 설명 참고).
resource "azurerm_subnet" "this" {
  name                 = "${var.server_name}-subnet"
  resource_group_name  = local.vnet_resource_group_name
  virtual_network_name = local.vnet_name
  address_prefixes     = [local.subnet_cidr]

  delegation {
    name = "postgresql-flexible-server-delegation"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# Private DNS Zone 이름은 Azure 요구사항상 "postgres.database.azure.com"으로 끝나야 한다.
resource "azurerm_private_dns_zone" "this" {
  name                = "${var.server_name}.postgres.database.azure.com"
  resource_group_name = local.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  name                  = "${var.server_name}-dns-link"
  resource_group_name   = local.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.this.name
  virtual_network_id    = local.vnet_id
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                = var.server_name
  resource_group_name = local.resource_group_name
  location            = local.location

  administrator_login    = var.admin_login
  administrator_password = var.admin_password

  sku_name              = "B_Standard_B1ms" # 맵핑 문서 "사양 완전 제외" — PostgreSQL 최소 Burstable(B1s 없음)
  version               = "15"
  storage_mb            = 32768 # 32GB, Flexible Server 최소값(MySQL의 20GB보다 큼)
  backup_retention_days = 7

  delegated_subnet_id = azurerm_subnet.this.id
  private_dns_zone_id = azurerm_private_dns_zone.this.id

  tags = var.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.this]
}

resource "azurerm_postgresql_flexible_server_database" "this" {
  name      = var.database_name
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
