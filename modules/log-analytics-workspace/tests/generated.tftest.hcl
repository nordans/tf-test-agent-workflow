mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.OperationalInsights/workspaces/log-test"
    }
  }
}

variables {
  name                = "law-test"
  resource_group_name = "rg-test"
  location            = "eastus"
}

run "default_configuration" {
  command = plan

  assert {
    condition     = azurerm_log_analytics_workspace.this.sku == "PerGB2018"
    error_message = "Expected SKU to be PerGB2018 by default."
  }

  assert {
    condition     = azurerm_log_analytics_workspace.this.retention_in_days == 30
    error_message = "Expected default retention_in_days to be 30."
  }

  assert {
    condition     = length(azurerm_log_analytics_workspace.this.tags) == 0
    error_message = "Expected tags to be empty by default."
  }
}

run "custom_override" {
  command = plan

  variables {
    retention_in_days = 90
    tags = {
      Environment = "test"
    }
  }

  assert {
    condition     = azurerm_log_analytics_workspace.this.retention_in_days == 90
    error_message = "Expected custom retention_in_days to be 90."
  }

  assert {
    condition     = azurerm_log_analytics_workspace.this.tags["Environment"] == "test"
    error_message = "Expected tags to include Environment = test."
  }
}

run "outputs" {
  command = plan

  assert {
    condition     = output.id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.OperationalInsights/workspaces/log-test"
    error_message = "Output id did not match expected mock workspace ID."
  }
}
