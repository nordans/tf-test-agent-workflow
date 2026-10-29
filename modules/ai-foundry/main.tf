resource "azurerm_cognitive_account" "this" {
  name                  = var.name
  resource_group_name   = var.resource_group_name
  location              = var.location
  custom_subdomain_name = var.custom_subdomain_name
  kind                  = "AIServices"
  sku_name              = "S0"

  project_management_enabled    = true
  public_network_access_enabled = false
  local_auth_enabled            = false

  identity {
    type = "SystemAssigned"
  }

  tags = merge(var.tags, { managed_by = "terraform" })
}
