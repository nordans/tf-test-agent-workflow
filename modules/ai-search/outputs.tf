output "id" {
  description = "Search service resource ID."
  value       = azurerm_search_service.this.id
}

output "name" {
  description = "Search service name."
  value       = azurerm_search_service.this.name
}

output "principal_id" {
  description = "Principal ID of the service's system-assigned identity."
  value       = azurerm_search_service.this.identity[0].principal_id
}
