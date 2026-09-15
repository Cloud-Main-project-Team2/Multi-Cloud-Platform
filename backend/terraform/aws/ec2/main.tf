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

data "aws_vpc" "default" {
  default = true
}

# aws_instance가 subnet_id 없이 기본 VPC의 "default-for-az" 서브넷을 암묵적으로 고르게 두면,
# 계정에 실제로 서브넷이 있어도 그 플래그가 안 붙어있으면 "No subnets found for the default VPC"
# 오류로 실패한다(2026-09-15 실사용 테스트에서 발견) — 기본 VPC의 서브넷을 명시적으로 조회해서 넘긴다.
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# 서브넷 자체가 하나도 없는 계정도 실제로 있었다(같은 실사용 테스트에서 발견 — 기본 VPC는
# 있는데 서브넷이 전부 삭제된 상태). 그럴 때만 서브넷을 하나 직접 만들어 자체 복구한다.
# 명시적 라우트 테이블 연결을 안 하면 AWS가 자동으로 VPC의 메인 라우트 테이블을 붙여준다 —
# 서브넷만 지워진 전형적인 경우엔 메인 라우트 테이블에 인터넷 게이트웨이 라우트가 이미 남아있어
# 이걸로 충분하다(인터넷 게이트웨이 자체를 조회/생성하는 로직은 일부러 안 둔다 — VPC에 이미
# 붙어있는 게이트웨이가 있으면 새로 만들다 충돌하기 쉽고, aws_internet_gateways처럼 목록형
# data source도 없어서 "있으면 재사용, 없으면 생성" 분기를 안전하게 만들기 어렵다).
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  needs_fallback_subnet = length(data.aws_subnets.default.ids) == 0
  resolved_subnet_id    = local.needs_fallback_subnet ? aws_subnet.fallback[0].id : data.aws_subnets.default.ids[0]
}

resource "aws_subnet" "fallback" {
  count                   = local.needs_fallback_subnet ? 1 : 0
  vpc_id                  = data.aws_vpc.default.id
  cidr_block              = cidrsubnet(data.aws_vpc.default.cidr_block, 8, 0)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "mcp-fallback-subnet" }
}

resource "aws_security_group" "this" {
  name_prefix = "${var.instance_name}-"
  vpc_id      = data.aws_vpc.default.id
  description = "Managed by multi-cloud-platform for ${var.instance_name}"

  # 인바운드 규칙은 공통 설정 항목이다 — var.inbound_rules가 비어 있으면 인바운드를
  # 아무것도 열지 않는다(azure/vm의 NSG와 같은 정책, 호출자가 명시한 규칙만 신뢰).
  dynamic "ingress" {
    for_each = var.inbound_rules
    content {
      from_port   = ingress.value.port
      to_port     = ingress.value.port
      protocol    = "tcp"
      cidr_blocks = [ingress.value.cidr]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = var.tags
}

resource "aws_instance" "this" {
  ami                    = local.resolved_ami_id
  instance_type          = var.instance_type
  subnet_id              = local.resolved_subnet_id
  vpc_security_group_ids = [aws_security_group.this.id]

  tags = merge(
    { Name = var.instance_name },
    var.tags,
  )
}
