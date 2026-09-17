# Azure Virtual Machine 생성 전용 모듈.
#
# 이 모듈은 "생성"만 담당한다(팀 정책: Terraform은 기존 리소스의 조회·시작·중지·삭제에
# 쓰지 않는다 — docs/01_API_Specification_v1.1.md 10절). 인증은 azurerm provider의 표준 방식인
# ARM_CLIENT_ID / ARM_CLIENT_SECRET / ARM_TENANT_ID / ARM_SUBSCRIPTION_ID 환경변수로만
# 주입한다. 이 변수 파일들에는 secret을 절대 선언하지 않는다.
#
# ## 기존 리소스 재사용 (2026-09-17 결정)
#
# var.existing_resource_group_name/existing_subnet_id/existing_network_security_group_id가
# 있으면 그 각각을 새로 만들지 않고 그대로 재사용한다(전부 optional, null이면 지금까지처럼
# 매번 새로 생성). VM용 서브넷/NSG는 위임(delegation)이 필요 없는 일반 리소스라 임의의 기존
# 서브넷·NSG를 재사용해도 안전하다(Azure DB 모듈처럼 서브넷 위임을 강제로 바꿔야 하는 경우와
# 다르다 — 그쪽은 기존 서브넷 재사용을 의도적으로 지원하지 않는다).

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }

  # 기본값은 로컬 backend다. job workspace(디렉터리) 자체가 job마다 격리되므로
  # state 충돌은 없지만, 컨테이너가 재시작되면 로컬 state가 사라질 수 있다.
  # 운영 배포 전 Azure Storage(azurerm backend) 같은 원격 backend로 반드시 교체해야 한다.
  # (backend/README.md "운영 환경 참고" 및 provisioning_jobs.terraform_state_ref 정책 참고)
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

# 존재/소유 확인은 apply 시점에 Azure API가 대신 해준다(credential이 이미 그 구독으로
# 스코프돼 있어 다른 구독의 리소스 그룹은 조회 자체가 안 된다 — 크로스 테넌트 위험 없음).
data "azurerm_resource_group" "existing" {
  count = var.existing_resource_group_name != null ? 1 : 0
  name  = var.existing_resource_group_name
}

locals {
  resource_group_name = var.existing_resource_group_name != null ? data.azurerm_resource_group.existing[0].name : azurerm_resource_group.this[0].name
  # 기존 RG를 재사용하면 그 RG 자체의 location을 따른다(var.location이 다르게 와도 무시) —
  # RG 안 리소스가 RG의 location과 달라도 Azure는 막지 않지만, 굳이 어긋나게 둘 이유가 없다.
  location = var.existing_resource_group_name != null ? data.azurerm_resource_group.existing[0].location : azurerm_resource_group.this[0].location
}

resource "azurerm_virtual_network" "this" {
  count               = var.existing_subnet_id == null ? 1 : 0
  name                = "${var.vm_name}-vnet"
  address_space       = ["10.0.0.0/16"]
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = var.tags
}

resource "azurerm_subnet" "this" {
  count                = var.existing_subnet_id == null ? 1 : 0
  name                 = "${var.vm_name}-subnet"
  resource_group_name  = local.resource_group_name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = ["10.0.1.0/24"]
}

locals {
  subnet_id = var.existing_subnet_id != null ? var.existing_subnet_id : azurerm_subnet.this[0].id
}

resource "azurerm_network_security_group" "this" {
  count               = var.existing_network_security_group_id == null ? 1 : 0
  name                = "${var.vm_name}-nsg"
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = var.tags

  # 인바운드 규칙은 공통 설정 항목이다 — var.inbound_rules가 비어 있으면 인바운드를
  # 아무것도 열지 않는다(호출자가 명시적으로 넘긴 규칙만 신뢰한다). 기존 NSG를 재사용하면
  # 이 규칙은 적용되지 않는다 — 그 NSG 자체의 규칙이 그대로 유효하다(호출자 책임).
  dynamic "security_rule" {
    for_each = var.inbound_rules
    content {
      name                       = "allow-${security_rule.value.port}"
      priority                   = 100 + security_rule.key
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_range     = tostring(security_rule.value.port)
      source_address_prefix      = security_rule.value.cidr
      destination_address_prefix = "*"
    }
  }
}

locals {
  network_security_group_id = var.existing_network_security_group_id != null ? var.existing_network_security_group_id : azurerm_network_security_group.this[0].id
}

resource "azurerm_public_ip" "this" {
  count               = var.create_public_ip ? 1 : 0
  name                = "${var.vm_name}-pip"
  location            = local.location
  resource_group_name = local.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_network_interface" "this" {
  name                = "${var.vm_name}-nic"
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = var.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = local.subnet_id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = var.create_public_ip ? azurerm_public_ip.this[0].id : null
  }
}

resource "azurerm_network_interface_security_group_association" "this" {
  network_interface_id      = azurerm_network_interface.this.id
  network_security_group_id = local.network_security_group_id
}

resource "azurerm_linux_virtual_machine" "this" {
  name                  = var.vm_name
  resource_group_name   = local.resource_group_name
  location              = local.location
  size                  = var.vm_size
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.this.id]
  tags                  = var.tags

  # 화면설계서·provisioning.js 기준 — Azure VM은 사용자명/비밀번호로 인증한다(SSH 키 아님).
  # admin_password는 TF_VAR_admin_password 환경변수로만 주입되고 이 파일에는 없다.
  disable_password_authentication = false
  admin_password                  = var.admin_password

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = var.image_publisher
    offer     = var.image_offer
    sku       = var.image_sku
    version   = var.image_version
  }
}
