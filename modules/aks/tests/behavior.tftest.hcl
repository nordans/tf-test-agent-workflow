mock_provider "azurerm" {
  override_during = plan
}

variables {
  name                = "aks-default"
  resource_group_name = "rg-default"
  location            = "westeurope"
  dns_prefix          = "aksdefault"
  tenant_id           = "00000000-0000-0000-0000-000000000003"
}

run "caller_input_wiring" {
  command = plan

  variables {
    name                = "aks-review"
    resource_group_name = "rg-review"
    location            = "northeurope"
    dns_prefix          = "aksreview"
    tenant_id           = "00000000-0000-0000-0000-000000000004"
    node_count          = 3
    vm_size             = "Standard_D4s_v5"
    tags                = { Environment = "review", Team = "platform" }
  }

  assert {
    condition     = azurerm_kubernetes_cluster.this.name == var.name
    error_message = "The caller's cluster name must be forwarded."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.resource_group_name == var.resource_group_name
    error_message = "The caller's resource group must be forwarded."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.location == var.location
    error_message = "The caller's region must be forwarded."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.dns_prefix == var.dns_prefix
    error_message = "The caller's DNS prefix must be forwarded."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.azure_active_directory_role_based_access_control[0].tenant_id == var.tenant_id
    error_message = "The configured Entra tenant must follow the caller."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.default_node_pool[0].node_count == 3
    error_message = "The caller's node count must be forwarded."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.default_node_pool[0].vm_size == var.vm_size
    error_message = "The caller's VM size must be forwarded."
  }
  assert {
    condition     = lookup(azurerm_kubernetes_cluster.this.tags, "Environment", "") == "review"
    error_message = "The caller's Environment tag must be forwarded."
  }
  assert {
    condition     = lookup(azurerm_kubernetes_cluster.this.tags, "Team", "") == "platform"
    error_message = "The caller's Team tag must be forwarded."
  }
}

run "default_tags_and_fixed_settings" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.name == var.name
    error_message = "The default scenario must also forward its distinct name."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.resource_group_name == var.resource_group_name
    error_message = "The default scenario must forward its distinct resource group."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.location == var.location
    error_message = "The default scenario must forward its region."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.dns_prefix == var.dns_prefix
    error_message = "The default scenario must forward its DNS prefix."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.azure_active_directory_role_based_access_control[0].tenant_id == var.tenant_id
    error_message = "The default scenario must forward its Entra tenant."
  }
  assert {
    condition     = length(azurerm_kubernetes_cluster.this.tags) == 0
    error_message = "Tags must be empty when the caller omits them."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.identity[0].type == "SystemAssigned"
    error_message = "The cluster must use a system-assigned identity."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.default_node_pool[0].name == "system"
    error_message = "The system pool name must remain stable."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.node_provisioning_profile[0].mode == "Manual"
    error_message = "The explicitly managed pool must use manual provisioning."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].network_plugin == "azure"
    error_message = "Azure CNI must remain selected."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].network_plugin_mode == "overlay"
    error_message = "CNI overlay mode must remain selected."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].load_balancer_sku == "standard"
    error_message = "The standard load balancer SKU must remain selected."
  }
}
