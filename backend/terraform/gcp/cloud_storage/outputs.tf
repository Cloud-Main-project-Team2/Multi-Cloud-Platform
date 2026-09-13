output "bucket_name" {
  value = google_storage_bucket.this.name
}

output "self_link" {
  value = google_storage_bucket.this.self_link
}

output "region" {
  # google_storage_bucket.this.location은 GCS가 대문자로 정규화해 돌려준다(예: "ASIA-NORTHEAST3")
  # — 앱 전역에서 region은 소문자로 저장하는 관례(compute_vm의 zone과 같은 이유)라 입력값을
  # 그대로 되돌려준다.
  value = var.region
}

output "storage_class" {
  value = google_storage_bucket.this.storage_class
}
