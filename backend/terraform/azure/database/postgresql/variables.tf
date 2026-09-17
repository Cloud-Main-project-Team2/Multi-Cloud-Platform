variable "resource_group_name" {
  description = "이 job 전용으로 생성할 리소스 그룹 이름. provisioning_jobs.workspace_name에서 파생된다."
  type        = string
}

variable "location" {
  description = "Azure region. 예: koreacentral, eastus."
  type        = string
}

variable "server_name" {
  description = "PostgreSQL Flexible Server 이름. Azure 전역에서 고유해야 한다."
  type        = string
}

variable "admin_login" {
  description = "관리자 계정명(공통 설정 — 사용자 입력)."
  type        = string
}

variable "admin_password" {
  description = "관리자 비밀번호. 반드시 환경변수(TF_VAR_admin_password)로만 주입한다."
  type        = string
  sensitive   = true
}

variable "database_name" {
  description = "생성할 데이터베이스 이름."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "existing_resource_group_name" {
  description = "기존 리소스 그룹을 재사용하려면 그 이름. null이면 var.resource_group_name으로 새로 만든다."
  type        = string
  default     = null
}

variable "existing_vnet_id" {
  description = "기존 VNet을 재사용하려면 그 ARM 리소스 ID. null이면 VNet을 새로 만든다. 재사용해도 서브넷 자체는 항상 이 모듈이 새로 만든다(위임 때문에 기존 서브넷은 재사용하지 않는다)."
  type        = string
  default     = null
}

variable "existing_vnet_subnet_cidr" {
  description = "existing_vnet_id 재사용 시 새로 만들 전용 서브넷의 CIDR(그 VNet의 주소 공간과 안 겹치게 직접 지정 권장). null이면 그 VNet의 첫 주소 공간에서 자동으로 하나 추정한다 — 이미 쓰이고 있는 대역이면 apply가 실패한다."
  type        = string
  default     = null
}
