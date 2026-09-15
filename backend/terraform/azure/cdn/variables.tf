variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "profile_name" {
  type = string
}

variable "endpoint_name" {
  type = string
}

variable "origin_group_name" {
  type = string
}

variable "origin_name" {
  type = string
}

variable "route_name" {
  type = string
}

variable "origin_host_name" {
  type = string
}

variable "health_probe_path" {
  type = string
}

variable "health_probe_interval_seconds" {
  type = number
}

# route.supported_protocols(뷰어가 Front Door에 접속하는 프로토콜). https_redirect_enabled=true면
# 항상 ["Http", "Https"] — app/azure_cdn_provisioning.py의 build_tfvars()가 강제한다.
variable "supported_protocols" {
  type = list(string)
}

# route.forwarding_protocol — 오리진으로 실제 전달하는 프로토콜(뷰어 프로토콜과는 다른 축).
variable "forwarding_protocol" {
  type = string
}

variable "https_redirect_enabled" {
  type = bool
}

variable "query_string_caching_behavior" {
  type = string
}

variable "compression_enabled" {
  type = bool
}

variable "content_types_to_compress" {
  type = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
