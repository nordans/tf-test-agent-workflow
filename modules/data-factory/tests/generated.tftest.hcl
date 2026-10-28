mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_data_factory" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.DataFactory/factories/df-test"
      identity = {
        principal_id = "00000000-0000-0000-0000-000000000001"
        tenant_id    = "00000000-0000-0000-0000-000000000002"
      }
    }
  }
  mock_resource "azurerm_data_factory_linked_service_data_lake_storage_gen2" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.DataFactory/factories/df-test/linkedService/ls-logs"
    }
  }
  mock_resource "azurerm_monitor_diagnostic_setting" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/microsoft.insights/diagnosticSettings/diag-df-test"
    }
  }
}

run "defaults" {
  command = plan

  variables {
    name                       = "df-test"
    resource_group_name        = "rg-test"
    location                   = "westeurope"
    log_analytics_workspace_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.OperationalInsights/workspaces/law-test"
  }

  assert {
    condition     = azurerm_data_factory.this.public_network_enabled == true
    error_message = "Expected public_network_enabled to default to true."
  }

  assert {
    condition     = azurerm_data_factory.this.identity[0].type == "SystemAssigned"
    error_message = "Expected SystemAssigned identity."
  }

  assert {
    condition     = output.id == azurerm_data_factory.this.id
    error_message = "Output id must match the data factory resource ID."
  }

  assert {
    condition     = output.principal_id == azurerm_data_factory.this.identity[0].principal_id
    error_message = "Output principal_id must match the data factory identity principal ID."
  }

  assert {
    condition     = length(azurerm_data_factory_linked_service_data_lake_storage_gen2.this) == 0
    error_message = "No linked storage services should be planned by default."
  }

  assert {
    condition     = length(azurerm_data_factory.this.tags) == 0
    error_message = "Default tags must be empty."
  }

  assert {
    condition     = azurerm_monitor_diagnostic_setting.this.name == "diag-df-test"
    error_message = "Diagnostic setting name must follow the factory name."
  }

  assert {
    condition     = azurerm_monitor_diagnostic_setting.this.target_resource_id == azurerm_data_factory.this.id
    error_message = "Diagnostics must target this factory."
  }

  assert {
    condition     = azurerm_monitor_diagnostic_setting.this.log_analytics_workspace_id == var.log_analytics_workspace_id
    error_message = "Diagnostics must target the caller workspace."
  }

  assert {
    condition     = toset([for entry in azurerm_monitor_diagnostic_setting.this.enabled_log : entry.category]) == toset(["PipelineRuns", "ActivityRuns"])
    error_message = "Both configured diagnostic log categories must be enabled."
  }
}

run "overrides" {
  command = plan

  variables {
    name                       = "df-test"
    resource_group_name        = "rg-test"
    location                   = "westeurope"
    public_network_enabled     = false
    log_analytics_workspace_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.OperationalInsights/workspaces/law-test"
    linked_storage_accounts = {
      logs = "https://stlogs.dfs.core.windows.net/"
    }
    tags = {
      Environment = "test"
    }
  }

  assert {
    condition     = azurerm_data_factory.this.public_network_enabled == false
    error_message = "Expected public_network_enabled to be overridable to false."
  }

  assert {
    condition     = lookup(azurerm_data_factory.this.tags, "Environment", "") == "test"
    error_message = "Expected tags to be correctly applied."
  }

  assert {
    condition     = try(azurerm_data_factory_linked_service_data_lake_storage_gen2.this["logs"].url, "") == "https://stlogs.dfs.core.windows.net/"
    error_message = "Linked storage account URL should match input."
  }

  assert {
    condition     = length(azurerm_data_factory_linked_service_data_lake_storage_gen2.this) == 1
    error_message = "Exactly one linked storage service must be planned."
  }

  assert {
    condition     = try(azurerm_data_factory_linked_service_data_lake_storage_gen2.this["logs"].name, "") == "ls-logs"
    error_message = "Linked service names must derive from map keys."
  }

  assert {
    condition     = try(azurerm_data_factory_linked_service_data_lake_storage_gen2.this["logs"].data_factory_id, "") == azurerm_data_factory.this.id
    error_message = "Linked storage must belong to this factory."
  }

  assert {
    condition     = try(azurerm_data_factory_linked_service_data_lake_storage_gen2.this["logs"].use_managed_identity, false) == true
    error_message = "Linked storage must use managed identity."
  }
}
