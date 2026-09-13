variable "project_id" {
  description = "GCP project ID (cloud_accounts.external_account_id)"
  type        = string
}

variable "region" {
  description = "GCS 버킷 location(단일 리전), 예: asia-northeast3"
  type        = string
}

variable "bucket_name" {
  description = "전역에서 고유해야 하는 GCS 버킷 이름 — Compute/Cloud SQL과 달리 mcp- 접두사를 붙이지 않는다"
  type        = string
}

variable "storage_class" {
  description = "GCS storage class, 예: STANDARD / NEARLINE / COLDLINE / ARCHIVE"
  type        = string
}

variable "labels" {
  description = "버킷에 붙일 라벨"
  type        = map(string)
  default     = {}
}
