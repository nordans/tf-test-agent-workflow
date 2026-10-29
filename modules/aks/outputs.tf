output "id" {
  description = "AKS cluster resource ID."
  value       = azurerm_kubernetes_cluster.this.id
}

output "private_fqdn" {
  description = "Private API server DNS name."
  value       = azurerm_kubernetes_cluster.this.private_fqdn
}

output "principal_id" {
  description = "Principal ID of the cluster's system-assigned identity."
  value       = azurerm_kubernetes_cluster.this.identity[0].principal_id
}
