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

# ami_id를 비워두면(null) var.image_owner/var.image_name_filter로 최신 AMI를 자동으로 찾는다 —
# AMI ID는 리전마다/시점마다 달라서 사람이 직접 입력하면 틀리기 쉽다. 두 변수는
# app/aws_provisioning.py의 IMAGE_FAMILIES가 사용자가 고른 OS 계열(기본값 Amazon Linux 2023,
# 2026-09-21부터 Ubuntu 22.04도 선택 가능)에 맞춰 채운다.
#
# Amazon Linux 2023 이름 패턴은 "al2023-ami-2023."으로 시작해야 한다(기본값) — "al2023-ami-*-x86_64"
# 처럼 느슨하게 두면 "al2023-ami-minimal-2023...-x86_64"(minimal 변형)도 매치되고, most_recent가
# 그걸 고를 수 있다. minimal 변형엔 SSM Agent가 안 들어 있어서, SSM Session Manager로 붙는 이
# 모듈의 전제(SSH 키 페어 대신 SSM 접속, 2026-09-15 결정)가 깨진다 — IAM 역할·보안그룹·VPC
# 라우팅이 전부 정상인데도 인스턴스가 SSM에 영영 등록되지 않는 형태로 나타난다(실제로 겪은 버그:
# 생성은 성공하지만 `aws ssm start-session`이 계속 TargetNotConnected로 실패). Canonical의 공식
# Ubuntu AMI(owner 099720109477)는 최신 릴리스부터 SSM Agent를 snap으로 기본 포함하므로 같은
# 문제가 없다 — 다만 Canonical이 배포 방식을 바꾸면 재확인이 필요하다.
data "aws_ami" "selected" {
  count       = var.ami_id == null ? 1 : 0
  most_recent = true
  owners      = [var.image_owner]

  filter {
    name   = "name"
    values = [var.image_name_filter]
  }
}

locals {
  resolved_ami_id = var.ami_id != null ? var.ami_id : data.aws_ami.selected[0].id
}

# var.vpc_id가 있으면 그 VPC를 그대로 쓰고(존재/소유 확인은 AWS API 자체가 해준다 — credential이
# 이미 그 계정으로 스코프돼 있어 타 계정 VPC는 조회 자체가 안 된다), 없으면 지금까지처럼 계정의
# 기본(default) VPC를 쓴다.
data "aws_vpc" "default" {
  count   = var.vpc_id == null ? 1 : 0
  default = true
}

data "aws_vpc" "selected" {
  count = var.vpc_id != null ? 1 : 0
  id    = var.vpc_id
}

locals {
  vpc_id         = var.vpc_id != null ? data.aws_vpc.selected[0].id : data.aws_vpc.default[0].id
  vpc_cidr_block = var.vpc_id != null ? data.aws_vpc.selected[0].cidr_block : data.aws_vpc.default[0].cidr_block
}

# aws_instance가 subnet_id 없이 기본 VPC의 "default-for-az" 서브넷을 암묵적으로 고르게 두면,
# 계정에 실제로 서브넷이 있어도 그 플래그가 안 붙어있으면 "No subnets found for the default VPC"
# 오류로 실패한다(2026-09-15 실사용 테스트에서 발견) — VPC(기본 또는 var.vpc_id로 지정한 것)의
# 서브넷을 명시적으로 조회해서 넘긴다. var.subnet_id가 오면 이 탐색 자체를 건너뛰고 그 서브넷을
# 그대로 쓴다(자동 생성/폴백 없음 — 서브넷을 이미 정확히 골라줬다는 뜻이므로).
data "aws_subnets" "selected" {
  filter {
    name   = "vpc-id"
    values = [local.vpc_id]
  }
}

# 서브넷 자체가 하나도 없는 계정도 실제로 있었다(같은 실사용 테스트에서 발견 — 기본 VPC는
# 있는데 서브넷이 전부 삭제된 상태). var.subnet_id가 없고 그 VPC에 서브넷도 하나 없을 때만
# 서브넷을 하나 직접 만들어 자체 복구한다. 명시적 라우트 테이블 연결을 안 하면 AWS가 자동으로
# VPC의 메인 라우트 테이블을 붙여준다 — 서브넷만 지워진 전형적인 경우엔 메인 라우트 테이블에
# 인터넷 게이트웨이 라우트가 이미 남아있어 이걸로 충분하다(인터넷 게이트웨이 자체를 조회/생성
# 하는 로직은 일부러 안 둔다 — VPC에 이미 붙어있는 게이트웨이가 있으면 새로 만들다 충돌하기
# 쉽고, aws_internet_gateways처럼 목록형 data source도 없어서 "있으면 재사용, 없으면 생성"
# 분기를 안전하게 만들기 어렵다).
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  needs_fallback_subnet = var.subnet_id == null && length(data.aws_subnets.selected.ids) == 0
  resolved_subnet_id = (
    var.subnet_id != null ? var.subnet_id :
    local.needs_fallback_subnet ? aws_subnet.fallback[0].id :
    data.aws_subnets.selected.ids[0]
  )
}

resource "aws_subnet" "fallback" {
  count                   = local.needs_fallback_subnet ? 1 : 0
  vpc_id                  = local.vpc_id
  cidr_block              = cidrsubnet(local.vpc_cidr_block, 8, 0)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "mcp-fallback-subnet" }
}

# var.security_group_id가 오면 그 보안 그룹을 그대로 쓰고 우리 전용 보안 그룹은 만들지 않는다 —
# 이 경우 var.inbound_rules는 적용되지 않는다(우리가 관리하는 SG가 아니므로 그 SG 자체의 규칙이
# 그대로 유효하다. 아웃바운드가 막혀 있으면 SSM 접속이 안 될 수 있다 — 호출자 책임).
resource "aws_security_group" "this" {
  count       = var.security_group_id == null ? 1 : 0
  name_prefix = "${var.instance_name}-"
  vpc_id      = local.vpc_id
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

locals {
  resolved_security_group_id = var.security_group_id != null ? var.security_group_id : aws_security_group.this[0].id
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
  subnet_id              = local.resolved_subnet_id
  vpc_security_group_ids = [local.resolved_security_group_id]
  iam_instance_profile   = aws_iam_instance_profile.ssm.name

  tags = merge(
    { Name = var.instance_name },
    var.tags,
  )
}
