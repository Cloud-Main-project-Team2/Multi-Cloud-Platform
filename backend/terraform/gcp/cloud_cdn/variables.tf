variable "project_id" {
  description = "GCP project ID (cloud_accounts.external_account_id)"
  type        = string
}

variable "instance_name" {
  description = "기본 이름 — 하위 리소스(url map/proxy/forwarding rule) 이름을 여기서 파생시킨다"
  type        = string
}

variable "bucket_name" {
  description = "이 CDN 전용으로 새로 만들 GCS 버킷 이름(mcp-cdn-{job_id} — job_id 기반이라 전역에서 항상 유일)"
  type        = string
}

