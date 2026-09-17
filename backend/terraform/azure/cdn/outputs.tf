output "endpoint_hostname" {
  value = azurerm_cdn_frontdoor_endpoint.this.host_name
}

output "resource_group_name" {
  value = local.resource_group_name
}

output "profile_id" {
  value = azurerm_cdn_frontdoor_profile.this.id
}

output "route_name" {
  value = azurerm_cdn_frontdoor_route.this.name
}
