# GCP Cloud Storage(Object Storage) 프로비저닝 모듈.
#
# job마다 독립된 워크스페이스 디렉터리(app/terraform_runner.py가 이 모듈을 복사해 만든다)에서
# 실행되며, 로컬 backend에 state를 남긴다(원격 backend 없음 — compute_vm/cloud_sql 모듈과 동일
# 전제). 인증은 `provider "google" {}`가 인자 없이 GOOGLE_APPLICATION_CREDENTIALS 환경변수
# (임시 파일)를 통해 ADC로 읽는다.
#
# 버킷 이름은 GCS 전역(프로젝트 무관)에서 유일해야 한다 — Compute 인스턴스/Cloud SQL 인스턴스와
# 달리 mcp- 접두사를 붙이지 않는다(맵핑 문서 "버킷/계정명(전역 고유, mcp- 프리픽스 없음)").
#
# 접근제어·중복성·버전관리는 맵핑 문서 "제외" 필드라 사용자 입력을 받지 않고 여기서 안전한
# 기본값으로 고정한다: uniform bucket-level access + public access prevention enforced(비공개
# 기본), versioning 비활성.

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
  region  = var.region
}

resource "google_storage_bucket" "this" {
  name          = var.bucket_name
  project       = var.project_id
  location      = var.region
  storage_class = var.storage_class
  labels        = var.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = false
  }
}
