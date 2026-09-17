variable "project_id" {
  description = "GCP project ID (cloud_accounts.external_account_id)"
  type        = string
}

variable "region" {
  description = "GCP region, e.g. asia-northeast3"
  type        = string
}

variable "instance_name" {
  description = "Cloud SQL instance name"
  type        = string
}

variable "database_version" {
  description = "Cloud SQL database_version, e.g. MYSQL_8_0 / POSTGRES_15 / SQLSERVER_2019_STANDARD"
  type        = string
}

variable "tier" {
  description = "Cloud SQL machine tier, e.g. db-f1-micro"
  type        = string
}

variable "admin_user" {
  description = "관리자 계정 이름(MySQL: root, PostgreSQL: postgres) — SQL Server는 google_sql_user 대신 인스턴스의 root_password로 sysadmin(sqlserver) 비밀번호를 설정하므로 쓰이지 않는다."
  type        = string
}

variable "create_admin_user" {
  description = "true면 google_sql_user로 admin_user 계정을 만든다(MySQL/PostgreSQL). false면 인스턴스 root_password로 SQL Server sysadmin 비밀번호를 설정한다."
  type        = bool
}

variable "root_password" {
  description = "관리자 계정 비밀번호. TF_VAR_root_password 환경변수로만 전달된다 — tfvars 파일에는 절대 쓰지 않는다."
  type        = string
  sensitive   = true
}

variable "labels" {
  description = "인스턴스에 붙일 라벨"
  type        = map(string)
  default     = {}
}

variable "network" {
  description = "기존 VPC 네트워크를 재사용하려면 그 이름 또는 self_link. null이면 이 job 전용 VPC를 새로 만든다. 재사용 시 그 VPC에 Cloud SQL Private Services Access가 이미 설정돼 있어야 한다(이 모듈이 피어링을 대신 만들지 않는다 — 모듈 상단 주석 참고)."
  type        = string
  default     = null
}
