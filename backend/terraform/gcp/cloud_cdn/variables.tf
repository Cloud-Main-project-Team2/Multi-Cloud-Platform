variable "project_id" {
  description = "GCP project ID (cloud_accounts.external_account_id)"
  type        = string
}

variable "instance_name" {
  description = "기본 이름 — 하위 리소스(url map/proxy/forwarding rule) 이름을 여기서 파생시킨다"
  type        = string
}

variable "bucket_name" {
  description = "백엔드로 쓸 GCS 버킷 이름 — create_bucket=true면 새로 만들 이름(mcp-cdn-{job_id}), false면 이미 있는 버킷 이름"
  type        = string
}

variable "create_bucket" {
  description = "true면 이 CDN 전용 버킷을 새로 생성, false면 bucket_name의 기존 버킷을 그대로 사용"
  type        = bool
  default     = true
}

