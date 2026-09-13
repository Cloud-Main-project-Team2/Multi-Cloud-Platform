terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# CloudFront는 전역 서비스지만 Terraform aws provider엔 리전 설정이 필요하다 — us-east-1로
# 고정한다(CloudFront API/ACM 인증서 요구사항과 같은 관례). 그래서 provider_spec에 region을
# 받지 않는다(app/aws_cloudfront_provisioning.py 참고). AWS 자격 증명은 이 워크스페이스를
# 실행하는 프로세스의 환경변수로만 주입한다(app/terraform_runner.py 참고).
provider "aws" {
  region = "us-east-1"
}

resource "aws_cloudfront_distribution" "this" {
  enabled = true
  comment = var.distribution_name

  # 오리진은 커스텀 HTTPS 오리진 하나만 지원한다(S3 오리진 전용 OAC 설정은 이번 범위 밖 —
  # app/aws_cloudfront_provisioning.py 결정 참고). S3를 오리진으로 쓰려면 버킷이 퍼블릭
  # 읽기를 허용하거나, 리전 도메인을 커스텀 오리진으로 넘기면 된다.
  origin {
    domain_name = var.origin_domain_name
    origin_id   = "primary"

    custom_origin_config {
      http_port              = 80
      https_port              = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "primary"
    viewer_protocol_policy = "redirect-to-https"

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # 커스텀 도메인/ACM 인증서는 이번 범위 밖 — CloudFront 기본 도메인(*.cloudfront.net)만 쓴다.
  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = var.tags
}
