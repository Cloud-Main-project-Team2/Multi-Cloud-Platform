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
  value = google_storage_bucket.cdn_bucket.location
}
