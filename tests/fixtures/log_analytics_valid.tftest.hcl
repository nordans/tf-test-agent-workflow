# Hand-written fixture for policy and sandbox checks, not an agent-generated artifact.
mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.OperationalInsights/workspaces/log-test"
    }
  }
}

variables {
  name                = "log-test"
  resource_group_name = "rg-test"
  location            = "westeurope"
}

run "defaults" {
  command = plan

  assert {
    condition     = azurerm_log_analytics_workspace.this.retention_in_days == 30
    error_message = "Default retention must be 30."
  }
  assert {
    condition     = azurerm_log_analytics_workspace.this.sku == "PerGB2018"
    error_message = "The configured SKU must be preserved."
  }
  assert {
    condition     = length(azurerm_log_analytics_workspace.this.tags) == 0
    error_message = "Default tags must be empty."
  }
}

run "overrides" {
  command = plan
  variables {
    retention_in_days = 90
    tags = {
      environment = "test"
    }
  }
  assert {
    condition     = azurerm_log_analytics_workspace.this.retention_in_days == 90
    error_message = "Retention must follow the caller override."
  }
  assert {
    condition     = azurerm_log_analytics_workspace.this.tags["environment"] == "test"
    error_message = "Tags must follow caller input."
  }
}

run "output_id" {
  command = plan
  assert {
    condition     = output.id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.OperationalInsights/workspaces/log-test"
    error_message = "The output must expose the resource ID."
  }
}
