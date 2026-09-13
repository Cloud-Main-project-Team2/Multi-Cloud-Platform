# GCP Cloud SQL 프로비저닝 모듈.
#
# job마다 독립된 워크스페이스 디렉터리(app/terraform_runner.py가 이 모듈을 복사해 만든다)에서
# 실행되며, 로컬 backend에 state를 남긴다(원격 backend 없음 — compute_vm 모듈과 동일 전제).
# 인증은 `provider "google" {}`가 인자 없이 GOOGLE_APPLICATION_CREDENTIALS 환경변수(임시 파일)를
# 통해 ADC로 읽는다.
#
# 관리자 계정 비밀번호 설정 방식은 엔진마다 다르다(Cloud SQL 제약):
# - MySQL/PostgreSQL: 인스턴스 생성 후 `google_sql_user`로 admin_user 계정을 만들고 비밀번호를 준다.
# - SQL Server: 별도 `google_sql_user`가 아니라 인스턴스 자체의 `root_password`로 기본 sysadmin
#   계정(sqlserver)의 비밀번호를 설정한다(Cloud SQL SQL Server의 고유한 방식).
#
# 데모/테스트 환경 전제로 공인 IP + 0.0.0.0/0 허용 네트워크를 연다(app/gcp_cloudsql_provisioning.py
# 참고, compute_vm 모듈의 "이 리소스 전용 방화벽 규칙" 원칙과 같은 방향). `deletion_protection`은
# 개발 편의를 위해 false로 둔다(운영 배포 시 재검토 필요).

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

resource "google_sql_database_instance" "this" {
  name                = var.instance_name
  project             = var.project_id
  region              = var.region
  database_version    = var.database_version
  root_password       = var.create_admin_user ? null : var.root_password
  deletion_protection = false

  settings {
    tier         = var.tier
    user_labels  = var.labels

    ip_configuration {
      ipv4_enabled = true

      authorized_networks {
        name  = "allow-all-demo"
        value = "0.0.0.0/0"
      }
    }
  }
}

resource "google_sql_user" "admin" {
  count    = var.create_admin_user ? 1 : 0
  name     = var.admin_user
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  password = var.root_password
}
