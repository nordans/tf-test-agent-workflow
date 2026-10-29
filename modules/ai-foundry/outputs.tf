output "id" {
  description = "Resource ID of the Foundry account."
  value       = azurerm_cognitive_account.this.id
}

output "endpoint" {
  description = "Endpoint reported for the Foundry account."
  value       = azurerm_cognitive_account.this.endpoint
}

output "principal_id" {
  description = "Principal ID of the system-assigned identity."
  value       = azurerm_cognitive_account.this.identity[0].principal_id
}
