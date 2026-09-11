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
  description = "사용할 AMI ID. null이면 최신 Amazon Linux 2023 AMI를 자동으로 찾는다"
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "추가 태그"
}
