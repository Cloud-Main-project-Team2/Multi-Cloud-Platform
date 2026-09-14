variable "resource_group_name" {
  description = "이 job 전용으로 생성할 리소스 그룹 이름. provisioning_jobs.workspace_name에서 파생된다."
  type        = string
}

variable "location" {
  description = "Azure region. 예: koreacentral, eastus."
  type        = string
}

variable "server_name" {
  description = "Azure SQL 논리 서버 이름. Azure 전역에서 고유해야 한다."
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
