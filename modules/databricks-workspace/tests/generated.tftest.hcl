mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_databricks_workspace" {
    defaults = {
      id                        = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Databricks/workspaces/ws-test"
      workspace_url             = "https://westeurope.azuredatabricks.net"
      managed_resource_group_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-managed"
      workspace_id              = "1234567890123456"
      disk_encryption_set_id    = null
      managed_disk_identity     = []
      storage_account_identity  = []
    }
  }
}

run "default_configuration" {
  command = plan

  variables {
    name                = "ws-default"
    resource_group_name = "rg-test"
    location            = "westeurope"
  }

  assert {
    condition     = azurerm_databricks_workspace.this.sku == "premium"
    error_message = "Default SKU should be premium."
  }

  assert {
    condition     = azurerm_databricks_workspace.this.public_network_access_enabled == true
    error_message = "Default public_network_access_enabled should be true."
  }

  assert {
    condition     = azurerm_databricks_workspace.this.managed_resource_group_name == "rg-test-ws-default-managed"
    error_message = "Managed resource group name should follow the expected naming convention."
  }

  assert {
    condition     = azurerm_databricks_workspace.this.custom_parameters[0].no_public_ip == true
    error_message = "no_public_ip should be set to true in custom_parameters."
  }

  assert {
    condition     = azurerm_databricks_workspace.this.name == "ws-default"
    error_message = "Workspace name must come from the caller."
  }

  assert {
    condition     = length(azurerm_databricks_workspace.this.tags) == 0
    error_message = "Tags must default to an empty map."
  }
}

run "invalid_sku_validation" {
  command = plan

  variables {
    name                = "ws-invalid"
    resource_group_name = "rg-test"
    location            = "westeurope"
    sku                 = "invalid-sku"
  }

  expect_failures = [
    var.sku
  ]
}

run "caller_overrides" {
  command = plan

  variables {
    name                          = "ws-custom"
    resource_group_name           = "rg-test"
    location                      = "westeurope"
    sku                           = "standard"
    public_network_access_enabled = false
    tags = {
      Environment = "prod"
    }
  }

  assert {
    condition     = azurerm_databricks_workspace.this.sku == "standard"
    error_message = "SKU should be overridable to standard."
  }

  assert {
    condition     = azurerm_databricks_workspace.this.public_network_access_enabled == false
    error_message = "public_network_access_enabled should be overridable to false."
  }

  assert {
    condition     = lookup(azurerm_databricks_workspace.this.tags, "Environment", "") == "prod"
    error_message = "Tags should be correctly propagated."
  }
}

run "output_wiring" {
  command = plan

  variables {
    name                = "ws-outputs"
    resource_group_name = "rg-test"
    location            = "westeurope"
  }

  assert {
    condition     = output.id == azurerm_databricks_workspace.this.id
    error_message = "Output id must be correctly wired."
  }

  assert {
    condition     = output.workspace_url == azurerm_databricks_workspace.this.workspace_url
    error_message = "Output workspace_url must be correctly wired."
  }
}
