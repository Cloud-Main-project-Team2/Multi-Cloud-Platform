# GCP Compute Engine VM 프로비저닝 모듈.
#
# job마다 독립된 워크스페이스 디렉터리(app/terraform_runner.py가 이 모듈을 복사해 만든다)에서
# 실행되며, 로컬 backend에 state를 남긴다(원격 backend 없음 — CLAUDE.md 참고). 인증은
# `provider "google" {}`가 인자 없이 GOOGLE_APPLICATION_CREDENTIALS 환경변수(app/terraform_runner.py가
# subprocess 호출에만 잠깐 노출하는 임시 파일)를 통해 ADC로 읽는다 — secret을 tfvars/변수로
# 절대 넘기지 않는다.
#
# 리소스 구성은 GCP_VM_생성_가이드.md(2026-09-01)를 따른다: Debian, pd-balanced 10GB 부팅 디스크,
# 기본(default) 네트워크의 ephemeral 외부 IP. 방화벽은 프로젝트의 default-allow-http/ssh 존재 여부에
# 기대지 않고, 이 VM에만 적용되는 전용 규칙(google_compute_firewall)을 job마다 함께 만든다
# (2026-09-11 결정, CLAUDE.md 참고).
#
# 이미지는 debian-12를 쓴다(2026-09-11 실사용 테스트에서 발견·수정): 원래 debian-11이었는데
# GCP가 해당 이미지 패밀리를 단종시켜 `debian-cloud` 프로젝트에서 내려갔다 — 실제 계정으로
# 끝까지(apply) 테스트해본 게 이번이 처음이라 아무도 못 보고 지나갔던 문제다.
#
# ## 인바운드 규칙은 var.inbound_rules를 실제로 반영한다(2026-09-16 결정 — 이전엔 죽은 UI였음)
#
# 프론트 ④ 공통 설정의 "인바운드 규칙"(HTTP/HTTPS 체크박스 + 커스텀 포트 추가)은 AWS
# (`terraform/aws/ec2/main.tf`)·Azure(`terraform/azure/vm/main.tf`)에서는 실제로 방화벽에
# 반영되는데, 이 GCP 모듈만 그 값을 아예 받지 않고 80번 포트를 무조건 고정으로 열고 있었다
# (실사용 테스트로 발견 — SSH 공개키 죽은 필드와 같은 종류의 문제). 이제 AWS/Azure와 동일하게
# `var.inbound_rules`가 비어 있으면 아무것도 안 열고, 사용자가 고른 포트·CIDR만 그대로 연다.
# 관리용 SSH(22, IAP 전용)는 이 목록과 무관하게 항상 별도로 고정 — 사용자가 선택하는 항목이
# 아니다(아래 `allow_iap_ssh` 참고).
#
# ## SSH는 IAP + OS Login으로 접속한다(2026-09-16 결정) — 키 페어·전체 공개 22번 포트 제거
#
# 프론트 폼에 "SSH Public Key" 입력칸이 있었지만 실제로는 백엔드/Terraform 어디에도 전달되지
# 않는 죽은 필드였다(실사용 테스트로 발견) — `google_compute_instance`에 `metadata.ssh-keys`가
# 애초에 없어 그 키를 등록할 방법 자체가 없었다. 그런데도 방화벽은 22번 포트를 `0.0.0.0/0`
# (전 세계)에 열어두고 있어서, "아무도 정상적으로는 못 들어가는데 공격 표면만 열려 있는" 상태였다.
#
# AWS가 같은 문제를 SSH 키 페어 대신 SSM Session Manager로 해결한 것(2026-09-15 결정,
# `terraform/aws/ec2/main.tf` 참고)과 같은 원칙으로, GCP는 자체 기능인 **IAP(Identity-Aware
# Proxy) TCP forwarding + OS Login**을 쓴다:
# - `enable-oslogin=TRUE`로 SSH 키 대신 GCP IAM 계정 자체가 로그인 인증 수단이 된다(키 관리 불필요).
# - 22번 포트는 전 세계가 아니라 **구글의 IAP 릴레이 대역(`35.235.240.0/20`)에서 오는 트래픽만**
#   허용한다 — 이 대역 밖에서는 22번 포트에 직접 접근 자체가 안 된다.
# - 80번 포트(실제 서비스 트래픽)는 그대로 `0.0.0.0/0`에 열어둔다 — 이건 관리용 접속이 아니라
#   외부 사용자가 쓰는 통로라 안 건드린다.
#
# **IAM 권한 부여는 이 모듈이 하지 않는다**(의도적) — `roles/compute.osLogin` +
# `roles/iap.tunnelResourceAccessor`를 누구에게 줄지는 이 GCP 프로젝트의 소유자가 콘솔에서 직접
# 정한다. CDN의 "기존 버킷 IAM은 자동으로 안 바꾼다"(app/gcp_cdn_provisioning.py 참고)는 결정과
# 같은 이유 — 우리 서비스 계정에 IAM 정책을 바꿀 권한까지 쥐어주지 않는 편이, 실수로 잘못된
# 대상에게 권한을 주는 사고를 원천적으로 막는다.

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
  tags         = ["http-server", "iap-ssh"]
  labels       = var.labels

  # SSH 키 대신 OS Login(IAM 계정 기반 인증)을 쓴다 — ssh-keys 메타데이터는 아예 안 둔다.
  metadata = {
    enable-oslogin = "TRUE"
  }

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {} # ephemeral external IP
  }
}

# 서비스 트래픽은 사용자가 고른 포트·CIDR만 연다 — 규칙마다 CIDR이 다를 수 있어(AWS/Azure와
# 같은 이유) 하나의 firewall에 여러 allow를 몰아넣지 않고 규칙 개수만큼 별도 리소스를 만든다.
# var.inbound_rules가 비어 있으면 이 리소스 자체가 하나도 안 만들어져 아무 포트도 안 열린다.
resource "google_compute_firewall" "allow_inbound" {
  for_each = { for idx, rule in var.inbound_rules : tostring(idx) => rule }

  name    = "${var.instance_name}-allow-${each.key}"
  project = var.project_id
  network = "default"

  allow {
    protocol = "tcp"
    ports    = [tostring(each.value.port)]
  }

  source_ranges = [each.value.cidr]
  target_tags   = ["http-server"]
}

# 관리용 SSH(22번)는 구글 IAP 릴레이 대역에서 오는 트래픽만 허용한다 — 이 대역은 전 세계 어디서든
# 직접 도달할 수 없고, `gcloud compute ssh --tunnel-through-iap`(또는 콘솔의 SSH 버튼)를 통해서만
# 트래픽이 나온다. 이 범위는 구글이 문서로 고정해 공개한 값이라 IP 하드코딩이 아니다.
resource "google_compute_firewall" "allow_iap_ssh" {
  name    = "${var.instance_name}-allow-iap-ssh"
  project = var.project_id
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["iap-ssh"]
}
