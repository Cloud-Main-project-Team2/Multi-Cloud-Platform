# Azure Database for PostgreSQL Flexible Server — 생성 전용 모듈 (비공개 전용, VNet 통합).
#
# mysql 서브모듈과 같은 원칙(팀 정책: AWS RDS/S3/CloudFront와 동일하게 기본값은 항상 안전하게 —
# GCP Cloud SQL의 퍼블릭+데모 구성을 따르지 않는다) — 생성 시점에 위임된 서브넷을 지정해 비공개로
# 만들고 이후 변경 불가능하므로, VNet/서브넷/Private DNS Zone을 이 모듈이 직접 갖춘다.

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

resource "azurerm_virtual_network" "this" {
  name                = "${var.server_name}-vnet"
  address_space       = ["10.0.0.0/16"]
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

# PostgreSQL Flexible Server 전용으로 위임된 서브넷 — 생성 후 변경 불가하므로 다른 리소스와 공유하지 않는다.
resource "azurerm_subnet" "this" {
  name                 = "${var.server_name}-subnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.0.1.0/24"]

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
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  name                  = "${var.server_name}-dns-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                = var.server_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

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
