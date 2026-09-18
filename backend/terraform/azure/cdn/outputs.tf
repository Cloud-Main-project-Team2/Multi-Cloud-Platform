output "endpoint_hostname" {
  value = azurerm_cdn_frontdoor_endpoint.this.host_name
}

output "resource_group_name" {
  value = local.resource_group_name
}

output "profile_id" {
  value = azurerm_cdn_frontdoor_profile.this.id
}

# 인벤토리 리소스 행의 식별자로 쓴다 — 동기화(app/providers/azure.py)가 최상위 Front Door
# profile만 열거할 수 있어 생성·동기화가 같은 이름으로 맞물리게 한다(routers/provisioning.py의
# `_resource_attrs` azure/cdn 참고).
output "profile_name" {
  value = azurerm_cdn_frontdoor_profile.this.name
}

output "route_name" {
  value = azurerm_cdn_frontdoor_route.this.name
}
