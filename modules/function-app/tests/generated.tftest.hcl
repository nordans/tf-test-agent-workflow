mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_service_plan" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Web/serverFarms/asp-test"
    }
  }
  mock_resource "azurerm_linux_function_app" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Web/sites/func-test"
      identity = {
        principal_id = "00000000-0000-0000-0000-000000000001"
        tenant_id    = "00000000-0000-0000-0000-000000000002"
      }
    }
  }
  mock_resource "azurerm_role_assignment" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Authorization/roleAssignments/00000000-0000-0000-0000-000000000003"
    }
  }
}

mock_provider "external" {
  override_during = plan
  mock_data "external" {
    defaults = {
      result = {
        revision = "mockbuild123"
      }
    }
  }
}

variables {
  name                 = "func-test"
  resource_group_name  = "rg-test"
  location             = "eastus"
  storage_account_name = "stgtest"
  storage_account_id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Storage/storageAccounts/stgtest"
}

run "defaults" {
  command = plan

  assert {
    condition     = azurerm_service_plan.this.os_type == "Linux"
    error_message = "Service plan OS type must be Linux."
  }
  assert {
    condition     = azurerm_service_plan.this.sku_name == "EP1"
    error_message = "Service plan SKU name must be EP1."
  }
  assert {
    condition     = azurerm_linux_function_app.this.https_only == true
    error_message = "HTTPS only must be enabled by default."
  }
  assert {
    condition     = azurerm_linux_function_app.this.public_network_access_enabled == true
    error_message = "Public network access must be enabled by default."
  }
  assert {
    condition     = azurerm_linux_function_app.this.storage_uses_managed_identity == true
    error_message = "Storage uses managed identity must be enabled."
  }
  assert {
    condition     = azurerm_linux_function_app.this.identity[0].type == "SystemAssigned"
    error_message = "Identity type must be SystemAssigned."
  }
  assert {
    condition     = azurerm_linux_function_app.this.site_config[0].minimum_tls_version == "1.2"
    error_message = "Minimum TLS version must be 1.2."
  }
  assert {
    condition     = azurerm_linux_function_app.this.site_config[0].application_stack[0].python_version == "3.12"
    error_message = "Default Python version must be 3.12."
  }






}

run "overrides" {
  command = plan

  variables {
    python_version                = "3.11"
    public_network_access_enabled = false
    app_settings = {
      CUSTOM_SETTING = "value"
      BUILD_REVISION = "conflicting_caller_value"
    }
    tags = {
      Environment = "test"
    }
  }

  assert {
    condition     = azurerm_linux_function_app.this.public_network_access_enabled == false
    error_message = "Public network access should be disabled when overridden."
  }
  assert {
    condition     = azurerm_linux_function_app.this.site_config[0].application_stack[0].python_version == "3.11"
    error_message = "Python version should be overrideable to 3.11."
  }
  assert {
    condition     = azurerm_linux_function_app.this.app_settings["CUSTOM_SETTING"] == "value"
    error_message = "Custom app settings must be passed through."
  }
  assert {
    condition     = azurerm_linux_function_app.this.app_settings["BUILD_REVISION"] == "mockbuild123"
    error_message = "BUILD_REVISION from external data source must take precedence in merge."
  }
  assert {
    condition     = lookup(azurerm_linux_function_app.this.tags, "Environment", "") == "test"
    error_message = "Custom tags must be applied correctly."
  }

  assert {
    condition     = lookup(azurerm_service_plan.this.tags, "Environment", "") == "test"
    error_message = "Custom tags must also reach the service plan."
  }
}

run "identity_and_storage_wiring" {
  command = plan
  assert {
    condition     = azurerm_linux_function_app.this.app_settings["BUILD_REVISION"] == "mockbuild123"
    error_message = "BUILD_REVISION must be populated from external build_info data."
  }
  assert {
    condition     = azurerm_role_assignment.runtime_storage.role_definition_name == "Storage Blob Data Owner"
    error_message = "Role definition name must be Storage Blob Data Owner."
  }
  assert {
    condition     = azurerm_role_assignment.runtime_storage.scope == var.storage_account_id
    error_message = "Role assignment scope must match storage account ID."
  }
  assert {
    condition     = azurerm_role_assignment.runtime_storage.principal_id == azurerm_linux_function_app.this.identity[0].principal_id
    error_message = "Role assignment principal ID must match the function app's managed identity."
  }
  assert {
    condition     = output.id == azurerm_linux_function_app.this.id
    error_message = "Output id must return the function app ID."
  }
  assert {
    condition     = output.principal_id == azurerm_linux_function_app.this.identity[0].principal_id
    error_message = "Output principal_id must return the managed identity principal ID."
  }

  assert {
    condition     = azurerm_linux_function_app.this.service_plan_id == azurerm_service_plan.this.id
    error_message = "The function app must reference its service plan."
  }

  assert {
    condition     = azurerm_linux_function_app.this.storage_account_name == var.storage_account_name
    error_message = "The runtime storage account must come from the input."
  }
}
