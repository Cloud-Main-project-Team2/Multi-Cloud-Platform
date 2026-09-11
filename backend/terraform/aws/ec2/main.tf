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

# ami_id를 비워두면(null) 최신 Amazon Linux 2023 AMI를 자동으로 찾는다 — AMI ID는
# 리전마다/시점마다 달라서 사람이 직접 입력하면 틀리기 쉽다.
data "aws_ami" "al2023" {
  count       = var.ami_id == null ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

locals {
  resolved_ami_id = var.ami_id != null ? var.ami_id : data.aws_ami.al2023[0].id
}

resource "aws_instance" "this" {
  ami           = local.resolved_ami_id
  instance_type = var.instance_type

  tags = merge(
    { Name = var.instance_name },
    var.tags,
  )
}
