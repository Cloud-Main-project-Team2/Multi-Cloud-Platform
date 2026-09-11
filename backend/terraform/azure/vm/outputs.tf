output "vm_id" {
  value = azurerm_linux_virtual_machine.this.id
}

output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "private_ip_address" {
  value = azurerm_network_interface.this.private_ip_address
}

output "public_ip_address" {
  value = var.create_public_ip ? azurerm_public_ip.this[0].ip_address : null
}
