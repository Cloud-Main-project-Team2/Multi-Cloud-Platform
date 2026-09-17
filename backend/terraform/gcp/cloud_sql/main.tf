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
# Service Networking API는 이 모듈이 직접 켠다(2026-09-16 결정 — 이전엔 "프로젝트에 미리
# 활성화돼 있어야 한다"는 전제 조건으로만 문서화하고 실제로 켜주지는 않아서, 새 프로젝트에서
# 실사용 테스트할 때 `SERVICE_DISABLED`(403)로 매번 막혔다). `google_project_service`로 활성화하고
# `google_service_networking_connection`이 그 뒤에 실행되도록 `depends_on`으로 순서를 강제한다.
#
# **`disable_on_destroy = false`가 필수다** — 기본값(true)으로 두면 이 job을 삭제(destroy)할 때
# API도 같이 꺼지는데, 이 앱은 job마다 독립된 Terraform 상태를 쓰면서도 같은 GCP 프로젝트를
# 여러 job이 공유한다(서로의 존재를 모름). 그래서 job A를 지울 때 API가 꺼지면, 같은 프로젝트의
# 다른 Cloud SQL(job B)이 쓰던 API가 갑자기 사라져 job B가 고장 날 수 있다 — 그래서 "켜는 건
# 자동으로, 끄는 건 절대 자동으로 하지 않는다."
#
# 사용하는 서비스 계정에는 여전히 `serviceusage.services.enable` 권한(예: Service Usage Admin
# 역할)이 있어야 한다 — 없으면 이 리소스 자체가 권한 오류로 실패한다(이건 이 모듈이 대신 처리해 줄
# 수 없는 진짜 IAM 전제 조건).

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

# Private Services Access(아래 VPC 피어링)에 필요한 API. disable_on_destroy=false 이유는 위
# 모듈 docstring 참고 — 다른 job(같은 프로젝트의 다른 Cloud SQL)이 쓰고 있을 수 있어 절대 자동으로
# 끄지 않는다.
resource "google_project_service" "servicenetworking" {
  project            = var.project_id
  service            = "servicenetworking.googleapis.com"
  disable_on_destroy = false
}

# var.network이 있으면 그 기존 VPC를 그대로 쓰고, 없으면 지금까지처럼 이 job 전용 VPC를 새로
# 만든다(2026-09-17 결정). Cloud SQL Private Services Access는 네트워크에 피어링된 IP 대역에서만
# private_ip_address를 할당하므로, 서브넷은 필요 없다(VM처럼 컴퓨트 인스턴스가 붙는 게 아님).
resource "google_compute_network" "this" {
  count                   = var.network == null ? 1 : 0
  name                    = "${var.instance_name}-vpc"
  project                 = var.project_id
  auto_create_subnetworks = false
}

locals {
  network_id = var.network != null ? var.network : google_compute_network.this[0].id
}

# Private Services Access용 예약 IP 대역(Google이 관리하는 서비스 네트워크와 피어링될 범위).
#
# **기존 VPC를 재사용할 땐 이 리소스도, 아래 피어링 연결도 만들지 않는다** — 이 서버는 job마다
# 격리된 Terraform state를 쓰는데(app/terraform_runner.py), 피어링 연결은 VPC 하나당 최대 1개만
# 존재할 수 있는 프로젝트 전역 공유 리소스다. 만약 이 job의 state가 그 피어링을 "우리 것"으로
# 관리해버리면, 나중에 이 job만 destroy해도 같은 VPC를 쓰는 다른 Cloud SQL 인스턴스까지 연결이
# 끊길 수 있다. 그래서 기존 VPC를 고르면 "그 VPC에 Private Services Access가 이미 설정돼 있다"고
# 가정만 하고(전제 조건, 문서화), 안 돼 있으면 인스턴스 생성 자체가 그냥 실패한다 — 몰래
# 가져오거나 바꾸지 않는다.
resource "google_compute_global_address" "private_ip_range" {
  count         = var.network == null ? 1 : 0
  name          = "${var.instance_name}-private-ip"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = local.network_id
}

# 이 VPC와 Google의 서비스 네트워크(servicenetworking) 간 피어링 연결 — Cloud SQL private IP가
# 이 피어링을 통해 붙는다(기존 VPC 재사용 시 생성하지 않는 이유는 위 설명 참고).
resource "google_service_networking_connection" "this" {
  count                    = var.network == null ? 1 : 0
  network                  = local.network_id
  service                  = "servicenetworking.googleapis.com"
  reserved_peering_ranges  = [google_compute_global_address.private_ip_range[0].name]

  # API가 켜진 다음에 피어링을 시도해야 한다 — google_compute_global_address.private_ip_range를
  # 통한 암묵적 의존만으로는 API 활성화 순서가 보장되지 않는다.
  depends_on = [google_project_service.servicenetworking]
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
      private_network = local.network_id
    }
  }

  # private_network가 실제로 쓰기 전에 서비스 네트워크 피어링이 먼저 완료돼 있어야 한다(기존
  # VPC를 재사용하면 이 리소스가 아예 안 만들어지므로 이 depends_on은 그냥 빈 목록이 된다 —
  # 그 경우 피어링이 이미 설정돼 있다고 가정한다는 뜻).
  depends_on = [google_service_networking_connection.this]
}

resource "google_sql_user" "admin" {
  count    = var.create_admin_user ? 1 : 0
  name     = var.admin_user
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  password = var.root_password
}
