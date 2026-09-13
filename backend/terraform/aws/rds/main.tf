terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# AWS 자격 증명은 이 워크스페이스를 실행하는 프로세스의 환경변수로만 주입한다
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN, master_password는
# TF_VAR_master_password) — app/terraform_runner.py, app/aws_rds_provisioning.py 참고.
provider "aws" {
  region = var.region
}

data "aws_vpc" "default" {
  default = true
}

# aws_db_subnet_group은 서로 다른 AZ의 서브넷이 최소 2개 필요하다 — 기본 VPC의 기본 서브넷을 그대로 쓴다.
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  # engine별 기본 포트 — provider_spec.engine 허용 목록(mysql/postgres)과 짝을 맞춘다.
  port = var.engine == "mysql" ? 3306 : 5432
}

resource "aws_db_subnet_group" "this" {
  name_prefix = "${var.instance_name}-"
  subnet_ids  = data.aws_subnets.default.ids
  tags        = var.tags
}

# 인터넷 전체가 아니라 같은 기본 VPC 안에서만 접근을 허용한다 — publicly_accessible=false와
# 짝을 맞춘 기본값(EC2 모듈의 inbound_rules처럼 호출자가 여는 게 아니라 이 리소스는 항상
# 비공개, app/aws_rds_provisioning.py 결정 참고). 같은 VPC의 EC2 인스턴스에서는 접속 가능하다.
resource "aws_security_group" "this" {
  name_prefix = "${var.instance_name}-"
  vpc_id      = data.aws_vpc.default.id
  description = "Managed by multi-cloud-platform for ${var.instance_name}"

  ingress {
    from_port   = local.port
    to_port     = local.port
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = var.tags
}

resource "aws_db_instance" "this" {
  identifier     = var.instance_name
  engine         = var.engine
  instance_class = var.instance_class
  # engine_version은 일부러 지정하지 않는다 — AWS가 그 시점의 기본 버전을 고른다
  # (backend/terraform/aws/ec2의 ami_id=null 자동 선택과 같은 원칙: 특정 마이너 버전을
  # 박아두면 리전/시점에 따라 폐기(deprecate)돼 apply가 실패하기 쉽다).
  allocated_storage = 20
  db_name           = var.db_name
  username          = "mcp_admin"
  password          = var.master_password

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  publicly_accessible    = false
  # 개발/테스트 정리 목적(app/dev_destroy_job.py) — 삭제 시 최종 스냅샷을 만들지 않는다.
  skip_final_snapshot = true

  tags = var.tags
}
