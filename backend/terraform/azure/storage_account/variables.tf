variable "resource_group_name" {
  description = "이 job 전용으로 생성할 리소스 그룹 이름. job마다 고유해야 하며 provisioning_jobs.workspace_name에서 파생된다(azure/vm과 동일 관례)."
  type        = string
}

variable "location" {
  description = "Azure region. 예: koreacentral, eastus."
  type        = string
}

variable "account_name" {
  description = <<-EOT
    Storage Account 이름. Azure 전역에서 고유해야 하고, 소문자/숫자만 허용되며(하이픈 불가)
    3~24자여야 한다 — app/azure_storage_provisioning.py가 이 규칙에 맞춰 조립해서 넘긴다.
  EOT
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
