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
#
# 이름 패턴은 "al2023-ami-2023."으로 시작해야 한다 — "al2023-ami-*-x86_64"처럼 느슨하게
# 두면 "al2023-ami-minimal-2023...-x86_64"(minimal 변형)도 매치되고, most_recent가 그걸
# 고를 수 있다. minimal 변형엔 SSM Agent가 안 들어 있어서, SSM Session Manager로 붙는
# 이 모듈의 전제(SSH 키 페어 대신 SSM 접속, 2026-09-15 결정)가 깨진다 — IAM 역할·보안그룹·
# VPC 라우팅이 전부 정상인데도 인스턴스가 SSM에 영영 등록되지 않는 형태로 나타난다(실제로
# 겪은 버그: 생성은 성공하지만 `aws ssm start-session`이 계속 TargetNotConnected로 실패).
data "aws_ami" "al2023" {
  count       = var.ami_id == null ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

locals {
  resolved_ami_id = var.ami_id != null ? var.ami_id : data.aws_ami.al2023[0].id
}

data "aws_vpc" "default" {
  default = true
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

# SSH 키 페어 대신 SSM Session Manager로 접속한다(정적 비밀키를 새로 만들지 않는 방향, 2026-09-15
# 결정 — 마이페이지 크리덴셜을 IAM Role/MFA로 옮기려는 방향과 같은 원칙). 이 역할/프로파일이
# 있어야 인스턴스의 SSM Agent가 Systems Manager에 등록되고, `aws ssm start-session`이 통한다.
data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ssm" {
  name_prefix        = "mcp-ssm-"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ssm" {
  name_prefix = "mcp-ssm-"
  role        = aws_iam_role.ssm.name
}

resource "aws_instance" "this" {
  ami                    = local.resolved_ami_id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.this.id]
  iam_instance_profile   = aws_iam_instance_profile.ssm.name

  tags = merge(
    { Name = var.instance_name },
    var.tags,
  )
}
