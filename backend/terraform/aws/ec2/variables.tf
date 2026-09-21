variable "region" {
  type        = string
  description = "인스턴스를 생성할 AWS 리전 (예: ap-northeast-2)"
}

variable "instance_type" {
  type        = string
  description = "인스턴스 타입 (예: t3.micro)"
}

variable "instance_name" {
  type        = string
  description = "Name 태그로 쓸 인스턴스 이름"
}

variable "ami_id" {
  type        = string
  default     = null
  description = "사용할 AMI ID. null이면 image_owner/image_name_filter로 최신 AMI를 자동으로 찾는다"
}

variable "image_owner" {
  type        = string
  default     = "amazon"
  description = "AMI 소유자 계정 ID/별칭. var.ami_id가 없을 때만 쓰인다(app/aws_provisioning.py의 IMAGE_FAMILIES가 채운다)."
}

variable "image_name_filter" {
  type        = string
  default     = "al2023-ami-2023.*-x86_64"
  description = "AMI 이름 필터 패턴. var.ami_id가 없을 때만 쓰인다 — owner와 짝을 이뤄 특정 OS 계열(Amazon Linux 2023/Ubuntu 22.04 등)을 고른다."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "추가 태그"
}

variable "inbound_rules" {
  type = list(object({
    port = number
    cidr = string
  }))
  default     = []
  description = "인바운드 규칙(공통 설정 항목). 비어 있으면 인바운드를 아무것도 열지 않는다(호출자가 명시적으로 넘긴 규칙만 신뢰)."
}

variable "vpc_id" {
  type        = string
  default     = null
  description = "기존 VPC를 재사용하려면 그 VPC ID. null이면 계정의 기본(default) VPC를 쓴다."
}

variable "subnet_id" {
  type        = string
  default     = null
  description = "기존 서브넷을 재사용하려면 그 서브넷 ID. null이면 선택된 VPC의 서브넷을 자동 탐색하고(없으면 새로 만든다)."
}

variable "security_group_id" {
  type        = string
  default     = null
  description = "기존 보안 그룹을 재사용하려면 그 ID. null이면 var.inbound_rules 기반으로 전용 보안 그룹을 새로 만든다. 기존 보안 그룹을 재사용하면 var.inbound_rules는 적용되지 않는다 — 그 보안 그룹 자체의 규칙이 그대로 쓰인다."
}
