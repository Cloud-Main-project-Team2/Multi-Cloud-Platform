variable "resource_group_name" {
  description = "이 job 전용으로 생성할 리소스 그룹 이름. job마다 고유해야 하며 provisioning_jobs.workspace_name에서 파생된다."
  type        = string
}

variable "location" {
  description = "Azure region. 예: koreacentral, eastus."
  type        = string
}

variable "vm_name" {
  description = "가상머신 이름(common_spec.name)."
  type        = string
}

variable "vm_size" {
  description = "Azure VM SKU. 예: Standard_B1s."
  type        = string
  default     = "Standard_B1s"
}

variable "admin_username" {
  description = "OS 관리자 계정명."
  type        = string
}

variable "ssh_public_key" {
  description = "관리자 계정에 등록할 SSH 공개키(OpenSSH 형식). 비밀키는 절대 이 변수로 전달하지 않는다."
  type        = string
}

variable "image_publisher" {
  type    = string
  default = "Canonical"
}

variable "image_offer" {
  type    = string
  default = "0001-com-ubuntu-server-jammy"
}

variable "image_sku" {
  type    = string
  default = "22_04-lts-gen2"
}

variable "image_version" {
  type    = string
  default = "latest"
}

variable "create_public_ip" {
  description = "공인 IP 생성 여부. false면 사설 IP만 갖는다."
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
