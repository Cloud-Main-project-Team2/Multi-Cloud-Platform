output "account_name" {
  value = azurerm_storage_account.this.name
}

output "account_id" {
  value = azurerm_storage_account.this.id
}

output "primary_blob_endpoint" {
  value = azurerm_storage_account.this.primary_blob_endpoint
}

output "resource_group_name" {
  value = local.resource_group_name
}
