# Azure Database for MySQL Flexible Server — 생성 전용 모듈 (비공개 전용, VNet 통합).
#
# 팀 정책(AWS RDS/S3/CloudFront와 동일): "사고 나면 되돌릴 수 없으니 기본값은 항상 안전하게" —
# 이 서비스는 실제 사용자 자격증명으로 실제 과금 리소스를 만들기 때문에, GCP Cloud SQL의
# 퍼블릭+데모 구성을 따르지 않고 AWS RDS와 같은 수준(비공개, 인터넷 노출 없음)으로 만든다.
# MySQL Flexible Server는 생성 시점에 위임된(delegated) 서브넷을 지정해야만 비공개로 만들 수
# 있고 이후 변경이 불가능하므로, VNet/서브넷/Private DNS Zone을 이 모듈이 직접 갖춘다
# (azure/vm과 같은 "job마다 자기 네트워크를 만든다" 관례).

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

# MySQL Flexible Server 전용으로 위임된 서브넷 — 생성 후 변경 불가하므로 이 서브넷은 다른
# 어떤 리소스와도 공유하지 않는다.
resource "azurerm_subnet" "this" {
  name                 = "${var.server_name}-subnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.0.1.0/24"]

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
resource "azurerm_private_dns_zone" "this" {
  name                = "${var.server_name}.mysql.database.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  name                  = "${var.server_name}-dns-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
}

resource "azurerm_mysql_flexible_server" "this" {
  name                = var.server_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  administrator_login    = var.admin_login
  administrator_password = var.admin_password

  sku_name              = "B_Standard_B1s" # 맵핑 문서 "사양 완전 제외" — 가장 작은 Burstable 고정
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
  resource_group_name = azurerm_resource_group.this.name
  server_name         = azurerm_mysql_flexible_server.this.name
  charset             = "utf8mb4"
  collation           = "utf8mb4_unicode_ci"
}
