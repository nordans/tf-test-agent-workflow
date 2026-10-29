resource "azurerm_kubernetes_cluster" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  dns_prefix          = var.dns_prefix

  private_cluster_enabled             = true
  private_cluster_public_fqdn_enabled = false
  local_account_disabled              = true
  role_based_access_control_enabled   = true

  default_node_pool {
    name                   = "system"
    node_count             = var.node_count
    vm_size                = var.vm_size
    node_public_ip_enabled = false
  }

  node_provisioning_profile {
    mode = "Manual"
  }

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    tenant_id          = var.tenant_id
    azure_rbac_enabled = true
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    load_balancer_sku   = "standard"
  }

  tags = var.tags
}
