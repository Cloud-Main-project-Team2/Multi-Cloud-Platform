# Azure SQL Database(mssql) — 생성 전용 모듈 (비공개 전용, Private Endpoint).
#
# mysql/postgresql 서브모듈과 같은 원칙(팀 정책: AWS RDS/S3/CloudFront와 동일하게 기본값은 항상
# 안전하게 — GCP Cloud SQL의 퍼블릭+데모 구성을 따르지 않는다). Azure SQL은 MySQL/PostgreSQL
# Flexible Server처럼 서브넷에 위임하는 방식이 아니라 **Private Endpoint**로 비공개 접근을
# 구성한다(서버 자체는 `public_network_access_enabled=false`로 퍼블릭 엔드포인트를 완전히 끈다).

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

# Private Endpoint 전용 서브넷 — 위임(delegation)은 필요 없지만, private endpoint 정책은 꺼야 한다.
resource "azurerm_subnet" "this" {
  name                            = "${var.server_name}-subnet"
  resource_group_name             = azurerm_resource_group.this.name
  virtual_network_name            = azurerm_virtual_network.this.name
  address_prefixes                = ["10.0.1.0/24"]
  private_endpoint_network_policies = "Disabled"
}

# Azure SQL Private Link의 Private DNS Zone 이름은 "privatelink.database.windows.net"으로 고정이다
# (Microsoft 요구사항 — MySQL/PostgreSQL처럼 서버 이름 기반이 아니라 모든 SQL 서버가 공유하는 고정 zone).
resource "azurerm_private_dns_zone" "this" {
  name                = "privatelink.database.windows.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  name                  = "${var.server_name}-dns-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
}

resource "azurerm_mssql_server" "this" {
  name                = var.server_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  version                      = "12.0"
  administrator_login          = var.admin_login
  administrator_login_password = var.admin_password
  minimum_tls_version          = "1.2"

  # Private Endpoint로만 접근한다 — 퍼블릭 엔드포인트 자체를 끈다(GCP Cloud SQL의 퍼블릭+데모
  # 구성을 따르지 않는다는 팀 결정, docs/Azure Storage·Database 프로비저닝 구현 결정사항 참고).
  public_network_access_enabled = false

  tags = var.tags
}

resource "azurerm_mssql_database" "this" {
  name      = var.database_name
  server_id = azurerm_mssql_server.this.id
  sku_name  = "Basic" # 맵핑 문서 "사양 완전 제외" — 가장 저렴한 등급 고정
  tags      = var.tags
}

resource "azurerm_private_endpoint" "this" {
  name                = "${var.server_name}-pe"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.this.id
  tags                = var.tags

  private_service_connection {
    name                           = "${var.server_name}-psc"
    private_connection_resource_id = azurerm_mssql_server.this.id
    subresource_names              = ["sqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "${var.server_name}-dns-zone-group"
    private_dns_zone_ids = [azurerm_private_dns_zone.this.id]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.this]
}
