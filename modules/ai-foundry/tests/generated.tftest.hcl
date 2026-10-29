mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_cognitive_account" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.CognitiveServices/accounts/acc-test"
      endpoint = "https://acc-test.cognitiveservices.azure.com/"
      identity = {
        principal_id = "00000000-0000-0000-0000-000000000001"
        tenant_id    = "00000000-0000-0000-0000-000000000002"
      }
    }
  }
}

run "name_validation_empty" {
  command = plan
  variables {
    name                  = ""
    resource_group_name   = "rg-test"
    location              = "westeurope"
    custom_subdomain_name = "sub-test"
  }
  expect_failures = [
    var.name
  ]
}

run "name_validation_whitespace" {
  command = plan
  variables {
    name                  = "   "
    resource_group_name   = "rg-test"
    location              = "westeurope"
    custom_subdomain_name = "sub-test"
  }
  expect_failures = [
    var.name
  ]
}

run "custom_subdomain_validation_empty" {
  command = plan
  variables {
    name                  = "acc-test"
    resource_group_name   = "rg-test"
    location              = "westeurope"
    custom_subdomain_name = ""
  }
  expect_failures = [
    var.custom_subdomain_name
  ]
}

run "custom_subdomain_validation_whitespace" {
  command = plan
  variables {
    name                  = "acc-test"
    resource_group_name   = "rg-test"
    location              = "westeurope"
    custom_subdomain_name = "   "
  }
  expect_failures = [
    var.custom_subdomain_name
  ]
}

run "default_configuration" {
  command = plan
  variables {
    name                  = "acc-test"
    resource_group_name   = "rg-test"
    location              = "westeurope"
    custom_subdomain_name = "sub-test"
    tags                  = { environment = "test" }
  }

  assert {
    condition     = azurerm_cognitive_account.this.kind == "AIServices"
    error_message = "The cognitive account kind must be AIServices."
  }

  assert {
    condition     = azurerm_cognitive_account.this.sku_name == "S0"
    error_message = "The cognitive account sku_name must be S0."
  }

  assert {
    condition     = azurerm_cognitive_account.this.public_network_access_enabled == false
    error_message = "Public network access must be disabled."
  }

  assert {
    condition     = azurerm_cognitive_account.this.local_auth_enabled == false
    error_message = "Local auth must be disabled."
  }

  assert {
    condition     = azurerm_cognitive_account.this.project_management_enabled == true
    error_message = "Project management must be enabled."
  }

  assert {
    condition     = azurerm_cognitive_account.this.identity[0].type == "SystemAssigned"
    error_message = "Identity type must be SystemAssigned."
  }

  assert {
    condition     = azurerm_cognitive_account.this.tags["managed_by"] == "terraform"
    error_message = "The managed_by tag must be set to terraform."
  }

  assert {
    condition     = azurerm_cognitive_account.this.tags["environment"] == "test"
    error_message = "Caller tags must be merged correctly."
  }

  assert {
    condition     = output.id == azurerm_cognitive_account.this.id
    error_message = "Output id does not match resource id."
  }

  assert {
    condition     = output.endpoint == azurerm_cognitive_account.this.endpoint
    error_message = "Output endpoint does not match resource endpoint."
  }

  assert {
    condition     = output.principal_id == azurerm_cognitive_account.this.identity[0].principal_id
    error_message = "Output principal_id does not match identity principal_id."
  }
}
