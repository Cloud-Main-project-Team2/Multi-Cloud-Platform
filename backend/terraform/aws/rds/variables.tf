variable "region" {
  type        = string
  description = "인스턴스를 생성할 AWS 리전 (예: ap-northeast-2)"
}

variable "instance_name" {
  type        = string
  description = "DB 인스턴스 식별자 및 서브넷 그룹/보안 그룹 이름 접두사로 쓸 이름"
}

variable "engine" {
  type        = string
  description = "DB 엔진 (mysql | postgres)"
}

variable "instance_class" {
  type        = string
  description = "인스턴스 클래스 (예: db.t3.micro)"
}

variable "db_name" {
  type        = string
  description = "생성할 기본 데이터베이스 이름"
}

variable "master_password" {
  type        = string
  sensitive   = true
  description = "마스터 사용자(mcp_admin) 비밀번호 — TF_VAR_master_password 환경변수로만 주입한다"
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "추가 태그"
}

variable "vpc_id" {
  type        = string
  default     = null
  description = "기존 VPC를 재사용하려면 그 VPC ID. null이면 계정의 기본(default) VPC를 쓴다. 이 VPC의 기존 서브넷을 읽기 전용으로 조회해 DB 서브넷 그룹을 구성한다."
}

variable "security_group_id" {
  type        = string
  default     = null
  description = "기존 보안 그룹을 재사용하려면 그 ID. null이면 '선택된 VPC CIDR만 허용'하는 전용 보안 그룹을 새로 만든다. 기존 보안 그룹을 재사용하면 이 정책은 적용되지 않는다 — 그 보안 그룹 자체의 규칙이 그대로 쓰인다."
}
