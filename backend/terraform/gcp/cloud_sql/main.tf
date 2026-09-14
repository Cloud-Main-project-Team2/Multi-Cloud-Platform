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
# 네트워크 정책(2026-09-14 개정 — 비공개 전용): 처음엔 데모 편의를 위해 공인 IP + 0.0.0.0/0 허용
# 네트워크를 열었으나, 팀 정책("사고 나면 되돌릴 수 없으니 기본값은 항상 안전하게" — AWS RDS/S3/
# CloudFront, Azure Storage/Database가 전부 따르는 원칙)에 맞춰 비공개 전용으로 바꿨다.
# `ipv4_enabled=false` + Private Services Access(VPC 피어링)로 전환 — MySQL/PostgreSQL/SQL Server
# 3엔진 모두 같은 방식을 지원해서(Azure처럼 엔진별로 다른 매커니즘이 필요하지 않다), 인스턴스
# 리소스 자체는 하나로 유지된다. `deletion_protection`은 개발 편의를 위해 false로 둔다(운영 배포
# 시 재검토 필요).
#
# 전제 조건(이 모듈이 자동으로 갖추지 않는 것): 프로젝트에 Service Networking API
# (`servicenetworking.googleapis.com`)가 이미 활성화돼 있어야 하고, 사용하는 서비스 계정에
# 피어링 연결을 만들 권한(예: Service Networking Admin)이 있어야 한다 — 안 그러면 apply 단계에서
# 권한 오류가 난다(Azure 리소스 프로바이더 미등록과 같은 종류의 전제 조건).

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

# job 전용 VPC — Cloud SQL Private Services Access는 이 네트워크에 피어링된 IP 대역에서만
# private_ip_address를 할당하므로, 서브넷은 필요 없다(VM처럼 컴퓨트 인스턴스가 붙는 게 아님).
resource "google_compute_network" "this" {
  name                    = "${var.instance_name}-vpc"
  project                 = var.project_id
  auto_create_subnetworks = false
}

# Private Services Access용 예약 IP 대역(Google이 관리하는 서비스 네트워크와 피어링될 범위).
resource "google_compute_global_address" "private_ip_range" {
  name          = "${var.instance_name}-private-ip"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.this.id
}

# 이 VPC와 Google의 서비스 네트워크(servicenetworking) 간 피어링 연결 — Cloud SQL private IP가
# 이 피어링을 통해 붙는다.
resource "google_service_networking_connection" "this" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
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
      ipv4_enabled    = false
      private_network = google_compute_network.this.id
    }
  }

  # private_network가 실제로 쓰기 전에 서비스 네트워크 피어링이 먼저 완료돼 있어야 한다.
  depends_on = [google_service_networking_connection.this]
}

resource "google_sql_user" "admin" {
  count    = var.create_admin_user ? 1 : 0
  name     = var.admin_user
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  password = var.root_password
}
