output "forwarding_rule_name" {
  value = google_compute_global_forwarding_rule.this.name
}

output "ip_address" {
  value = google_compute_global_forwarding_rule.this.ip_address
}

output "backend_bucket_name" {
  value = google_compute_backend_bucket.this.bucket_name
}

output "backend_bucket_location" {
  # create_bucket=false(기존 버킷 사용)면 이 모듈이 버킷을 만들지 않아 location을 모른다 — null.
  value = var.create_bucket ? google_storage_bucket.cdn_bucket[0].location : null
}

output "backend_bucket_created" {
  description = "이 apply가 백엔드 버킷을 새로 만들었는지(true) 기존 버킷을 재사용했는지(false)"
  value       = var.create_bucket
}
