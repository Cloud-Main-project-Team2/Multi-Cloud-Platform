# GCP Cloud CDN 프로비저닝 모듈.
#
# GCP엔 "CDN"이라는 독립 리소스가 없다 — HTTP(S) 로드밸런서 스택 위에 `enable_cdn`을 켜는
# 방식이다(app/gcp_cdn_provisioning.py 결정 참고). job마다 독립된 워크스페이스에서 실행되며,
# 인증은 `provider "google" {}`가 GOOGLE_APPLICATION_CREDENTIALS 환경변수(임시 파일)로 ADC를
# 통해 읽는다(다른 GCP 모듈과 동일).
#
# 백엔드 버킷은 `create_bucket`으로 선택한다(2026-09-14, 체크박스로 확장) — 기본은 이 CDN 전용으로
# 새로 만드는 쪽이다. 실사용 테스트에서 기존(범용) Cloud Storage 버킷을 오리진으로 연결했더니 그
# 버킷이 기본 비공개라 `AccessDenied`로 콘텐츠를 못 읽었던 적이 있어(범용 버킷에 공개 읽기를 나중에
# 붙이면 "이 버킷에 다른 민감한 파일도 같이 들어있으면 그것도 공개된다"는 위험), 기본값은
# app/gcp_cloudsql_provisioning.py(#47, DB마다 전용 VPC)와 같은 방향으로 "이 CDN 전용 버킷을 새로
# 생성"(이름 `mcp-cdn-{job_id}`, job_id 기반이라 항상 전역 유일 — app/gcp_cdn_provisioning.py
# 참고)이다. 다만 이미 있는 버킷을 그대로 쓰고 싶은 경우도 있어 `create_bucket=false` + 기존
# 버킷 이름(`bucket_name`)을 그대로 백엔드로 연결하는 경로도 지원한다.
#
# 기존 버킷 경로에서는 이 모듈이 그 버킷의 IAM(공개 읽기)을 **절대 건드리지 않는다**(2026-09-15
# 결정, app/gcp_cdn_provisioning.py 상단 문서 참고) — 우리가 만들지 않은 버킷의 보안 설정을
# 자동으로 바꾸면, 이름 오타 하나로 엉뚱한 버킷을 공개시키는 사고로 이어질 수 있다. 그래서 공개
# 읽기 IAM은 우리가 직접 만든(`create_bucket=true`) 버킷에만 부여하고, 기존 버킷은 사용자가 GCP
# 콘솔에서 미리 공개로 설정해 뒀는지 API 계층(`existing_bucket_public_ack`)에서 확인만 받는다 —
# 안 해놨다면 아래 backend_bucket이 `AccessDenied`로 실패할 뿐, 보안 사고로 이어지지 않는다.
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
  count                       = var.create_bucket ? 1 : 0
  name                        = var.bucket_name
  project                     = var.project_id
  location                    = "asia-northeast3"
  uniform_bucket_level_access = true
  force_destroy               = true # CDN 전용 버킷이라 안에 뭐가 있든 CDN과 함께 정리돼야 한다
}

# CDN이 원본을 읽으려면 백엔드 버킷은 공개 읽기여야 한다 — 하지만 이 리소스는 우리가 직접 만든
# 버킷(create_bucket=true)에만 만든다. 그 버킷은 처음부터 "CDN 전용"이라 공개해도 다른 용도와
# 섞일 위험이 없다(범용 Storage 버킷은 app/gcp_storage_provisioning.py가 항상 비공개로 만드는 것과
# 대조적). 기존 버킷(create_bucket=false)은 우리가 소유하지 않은 리소스라 IAM을 자동으로 바꾸지
# 않는다 — 사용자가 GCP 콘솔에서 미리 공개 읽기로 설정해 뒀어야 하며(API 계층
# existing_bucket_public_ack로 확인만 받음), 안 해놨다면 아래 backend_bucket이 AccessDenied로
# 실패한다(보안 사고가 아니라 안전한 방향의 실패).
resource "google_storage_bucket_iam_member" "public_read" {
  count      = var.create_bucket ? 1 : 0
  bucket     = var.bucket_name
  role       = "roles/storage.objectViewer"
  member     = "allUsers"
  depends_on = [google_storage_bucket.cdn_bucket]
}

resource "google_compute_backend_bucket" "this" {
  name             = var.instance_name
  project          = var.project_id
  bucket_name      = var.bucket_name
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
