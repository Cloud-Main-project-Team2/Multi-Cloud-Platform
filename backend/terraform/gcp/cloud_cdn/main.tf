# GCP Cloud CDN 프로비저닝 모듈.
#
# GCP엔 "CDN"이라는 독립 리소스가 없다 — HTTP(S) 로드밸런서 스택 위에 `enable_cdn`을 켜는
# 방식이다(app/gcp_cdn_provisioning.py 결정 참고). job마다 독립된 워크스페이스에서 실행되며,
# 인증은 `provider "google" {}`가 GOOGLE_APPLICATION_CREDENTIALS 환경변수(임시 파일)로 ADC를
# 통해 읽는다(다른 GCP 모듈과 동일).
#
# 백엔드 버킷은 기존 버킷을 받지 않고 이 CDN 전용으로 새로 만든다(2026-09-14 결정) — 실사용
# 테스트에서 기존(범용) Cloud Storage 버킷을 오리진으로 연결했더니 그 버킷이 기본 비공개라
# `AccessDenied`로 콘텐츠를 못 읽었다. 범용 버킷에 공개 읽기를 나중에 붙이면 "이 버킷에 다른
# 민감한 파일도 같이 넣여있으면 그것도 공개된다"는 위험이 있어, app/gcp_cloudsql_provisioning.py
# (#47, DB마다 전용 VPC)와 같은 방향으로 "공유 자원 재사용" 대신 "이 CDN 전용 버킷을 새로 생성 +
# 그 버킷에만 공개 읽기 부여"로 간다. 이름은 `mcp-cdn-{job_id}`(job_id 기반이라 항상 전역 유일 —
# app/gcp_cdn_provisioning.py 참고).
#
# 1차 범위는 백엔드 버킷(GCS)만 지원한다. 백엔드 서비스(인스턴스/NEG 기반)는 대상이 이미 떠
# 있어야 하고 헬스체크 등 구성이 더 복잡해 범위 밖이다.
#
# HTTPS/커스텀 도메인은 범위 밖이다 — Google 관리형 SSL 인증서는 DNS 검증 때문에 ACTIVE
# 상태가 되기까지 수십 분~몇 시간 걸릴 수 있어, 이 앱의 apply 타임아웃(기본 15분)을 넘기기
# 쉽다(AWS CloudFront가 커스텀 도메인/ACM 인증서를 범위 밖으로 뺀 것과 같은 이유). 그래서
# HTTP(포트 80)로만 서빙한다.
#
# 로드밸런서 리소스는 전역(global)이라 region이 없다 — CloudFront/Front Door와 동일 원칙.
# 버킷 자체는 GCS 특성상 location이 필요해 다른 GCP 리소스와 같은 기본 리전(asia-northeast3)에
# 고정한다(사용자 입력 없음 — 이 버킷은 이름도 위치도 전부 서버가 정한다).

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
}

resource "google_storage_bucket" "cdn_bucket" {
  name                        = var.bucket_name
  project                     = var.project_id
  location                    = "asia-northeast3"
  uniform_bucket_level_access = true
  force_destroy               = true # CDN 전용 버킷이라 안에 뭐가 있든 CDN과 함께 정리돼야 한다
}

# 이 버킷은 CDN이 공개로 서빙할 콘텐츠 전용이라 처음부터 공개 읽기를 부여한다(범용 Storage
# 버킷은 app/gcp_storage_provisioning.py가 항상 비공개로 만드는 것과 대조적 — 이 버킷은 다른
# 용도와 섞이지 않는 전용 버킷이라 공개해도 "다른 민감한 파일까지 같이 공개되는" 위험이 없다).
resource "google_storage_bucket_iam_member" "public_read" {
  bucket = google_storage_bucket.cdn_bucket.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

resource "google_compute_backend_bucket" "this" {
  name             = var.instance_name
  project          = var.project_id
  bucket_name      = google_storage_bucket.cdn_bucket.name
  enable_cdn       = true
  compression_mode = "AUTOMATIC"

  cdn_policy {
    cache_mode = "CACHE_ALL_STATIC"
  }
}

resource "google_compute_url_map" "this" {
  name            = "${var.instance_name}-urlmap"
  project         = var.project_id
  default_service = google_compute_backend_bucket.this.id
}

resource "google_compute_target_http_proxy" "this" {
  name    = "${var.instance_name}-http-proxy"
  project = var.project_id
  url_map = google_compute_url_map.this.id
}

resource "google_compute_global_forwarding_rule" "this" {
  name                  = "${var.instance_name}-fwd-rule"
  project               = var.project_id
  target                = google_compute_target_http_proxy.this.id
  port_range            = "80"
  load_balancing_scheme = "EXTERNAL"
}
