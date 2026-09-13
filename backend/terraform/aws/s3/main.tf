terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# AWS 자격 증명은 이 워크스페이스를 실행하는 프로세스의 환경변수
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN)로만 주입한다.
# .tf/.tfvars 파일에는 절대 자격 증명 값을 두지 않는다(app/terraform_runner.py 참고).
provider "aws" {
  region = var.region
}

resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name
  tags   = var.tags
}

# 퍼블릭 접근은 항상 전면 차단한다 — provider_spec에 옵트인 필드를 두지 않는다
# (app/aws_s3_provisioning.py의 "퍼블릭 접근" 결정 참고).
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id
  versioning_configuration {
    status = var.versioning_enabled ? "Enabled" : "Suspended"
  }
}
