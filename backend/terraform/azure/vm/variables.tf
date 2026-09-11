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

variable "admin_password" {
  description = <<-EOT
    OS 관리자 계정 비밀번호(화면설계서·provisioning.js 기준 — Azure VM은 SSH 키가 아니라
    사용자명/비밀번호 인증을 입력받는다). 반드시 환경변수(TF_VAR_admin_password)로만
    주입한다 — tfvars 파일에 평문으로 쓰지 않는다.
  EOT
  type        = string
  sensitive   = true
}

variable "inbound_rules" {
  description = "NSG에 열어줄 인바운드 규칙 목록(공통 설정 — 프론트 기본값은 22/tcp 하나)."
  type = list(object({
    port = number
    cidr = string
  }))
  default = []
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
