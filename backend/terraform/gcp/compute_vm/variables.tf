variable "project_id" {
  description = "GCP project ID (cloud_accounts.external_account_id)"
  type        = string
}

variable "region" {
  description = "GCP region, e.g. asia-northeast3"
  type        = string
}

variable "zone" {
  description = "GCP zone, e.g. asia-northeast3-a"
  type        = string
}

variable "machine_type" {
  description = "GCP machine type, e.g. e2-micro"
  type        = string
}

variable "instance_name" {
  description = "Compute Engine instance name (also used to derive the per-job firewall rule name)"
  type        = string
}

variable "labels" {
  description = "Labels applied to the instance"
  type        = map(string)
  default     = {}
}

variable "inbound_rules" {
  description = "인바운드 규칙(공통 설정 항목). 비어 있으면 인바운드를 아무것도 열지 않는다(호출자가 명시적으로 넘긴 규칙만 신뢰) — terraform/aws/ec2·terraform/azure/vm과 동일 정책."
  type = list(object({
    port = number
    cidr = string
  }))
  default = []
}
