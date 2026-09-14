# Azure Storage Account (Blob Storage, "Object Storage") 생성 전용 모듈.
#
# azure/vm 모듈과 같은 원칙(팀 정책: Terraform은 생성만 담당, 조회·시작·중지·삭제는 SDK로)과
# 같은 인증 방식(ARM_* 환경변수)을 따른다. 이 변수 파일들에는 secret을 절대 선언하지 않는다.
#
# 맵핑 문서(docs/멀티클라우드 3사 기능 맵핑…) 3절 "완전 제외" 결정에 따라 접근 제어·중복성·
# 버전관리는 사용자 입력을 받지 않고 아래처럼 안전한 기본값으로 고정한다:
#   - 접근 제어: 퍼블릭 액세스 전면 차단(S3 public_access_block, GCS public_access_prevention과
#     같은 원칙 — 버킷/계정 오정책발 데이터 유출은 되돌릴 수 없는 사고이므로 옵트인을 두지 않는다)
#   - 중복성: LRS(가장 저렴한 기본값 — 사용자 입력 없음)
#   - 버전관리: 비활성(Azure는 선택지 자체가 없다고 문서에 명시됨 — blob_properties 블록 자체를 생략)

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }

  # azure/vm과 동일 — 기본값은 로컬 backend. 운영 배포 전 원격 backend로 교체 필요
  # (backend/README.md "운영 환경 참고" 참고).
}

provider "azurerm" {
  features {}
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_storage_account" "this" {
  name                = var.account_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  account_tier             = "Standard" # 맵핑 문서: 스토리지 등급 입력은 GCP 전용 — Azure는 고정값
  account_replication_type = "LRS"      # 맵핑 문서 "중복성" 완전 제외 — 가장 저렴한 기본값 고정
  min_tls_version           = "TLS1_2"

  # 맵핑 문서 "접근 제어" 완전 제외 — 퍼블릭 차단이 실무 기본값(S3/GCS와 동일 원칙).
  allow_nested_items_to_be_public = false

  tags = var.tags
}
