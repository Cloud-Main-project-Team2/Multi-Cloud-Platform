# GCP Compute Engine VM 프로비저닝 모듈.
#
# job마다 독립된 워크스페이스 디렉터리(app/terraform_runner.py가 이 모듈을 복사해 만든다)에서
# 실행되며, 로컬 backend에 state를 남긴다(원격 backend 없음 — CLAUDE.md 참고). 인증은
# `provider "google" {}`가 인자 없이 GOOGLE_APPLICATION_CREDENTIALS 환경변수(app/terraform_runner.py가
# subprocess 호출에만 잠깐 노출하는 임시 파일)를 통해 ADC로 읽는다 — secret을 tfvars/변수로
# 절대 넘기지 않는다.
#
# 리소스 구성은 GCP_VM_생성_가이드.md(2026-09-01)를 따른다: Debian 11, pd-balanced 10GB 부팅 디스크,
# 기본(default) 네트워크의 ephemeral 외부 IP. 방화벽은 프로젝트의 default-allow-http/ssh 존재 여부에
# 기대지 않고, 이 VM에만 적용되는 전용 규칙(google_compute_firewall)을 job마다 함께 만든다
# (2026-09-11 결정, CLAUDE.md 참고) — 다른 VM에 영향 없이 22/80만 연다.

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
  zone    = var.zone
}

resource "google_compute_instance" "vm" {
  name         = var.instance_name
  project      = var.project_id
  zone         = var.zone
  machine_type = var.machine_type
  tags         = ["http-server", "ssh"]
  labels       = var.labels

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-11"
      size  = 10
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {} # ephemeral external IP
  }
}

resource "google_compute_firewall" "allow_web_ssh" {
  name    = "${var.instance_name}-allow-web-ssh"
  project = var.project_id
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22", "80"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["http-server", "ssh"]
}
