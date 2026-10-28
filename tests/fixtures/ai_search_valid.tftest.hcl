mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_search_service" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Search/searchServices/search-test"
    }
  }
}

variables {
  name = "search-test"
}

run "defaults" {
  command = plan
  assert {
    condition     = azurerm_search_service.this.sku == "basic"
    error_message = "Expected the default SKU."
  }
  assert {
    condition     = azurerm_search_service.this.public_network_access_enabled == false
    error_message = "Public access must remain disabled."
  }
  assert {
    condition     = length(azurerm_search_service.this.tags) == 0
    error_message = "Expected no default tags."
  }
}

run "overrides" {
  command = plan
  variables {
    sku  = "standard"
    tags = { Environment = "test" }
  }
  assert {
    condition     = azurerm_search_service.this.sku == "standard"
    error_message = "Expected the caller SKU."
  }
  assert {
    condition     = azurerm_search_service.this.tags["Environment"] == "test"
    error_message = "Expected caller tags."
  }
}

run "output_id" {
  command = plan
  assert {
    condition     = output.id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Search/searchServices/search-test"
    error_message = "Expected the mock resource ID."
  }
}
