mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_private_endpoint" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Network/privateEndpoints/pe-test"
      private_service_connection = {
        private_ip_address = "10.0.1.5"
      }
    }
  }
}

run "dns_zone_absent" {
  command = plan

  variables {
    name                = "test"
    resource_group_name = "rg-test"
    location            = "eastus"
    subnet_id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/subnet-test"
    target_resource_id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Storage/storageAccounts/st-test"
    subresource_name    = "blob"
    tags                = { Environment = "test" }
  }

  assert {
    condition     = azurerm_private_endpoint.this.name == "pe-test"
    error_message = "Private endpoint name is incorrect."
  }

  assert {
    condition     = length(azurerm_private_endpoint.this.private_dns_zone_group) == 0
    error_message = "Private DNS zone group should not be created when private_dns_zone_id is null."
  }

  assert {
    condition     = azurerm_private_endpoint.this.private_service_connection[0].private_connection_resource_id == var.target_resource_id
    error_message = "Private service connection target resource ID is incorrect."
  }

  assert {
    condition     = lookup(azurerm_private_endpoint.this.tags, "Environment", "") == "test"
    error_message = "Tags are incorrect."
  }

  assert {
    condition     = output.id == azurerm_private_endpoint.this.id
    error_message = "Output id does not match resource id."
  }

  assert {
    condition     = output.private_ip_address == "10.0.1.5"
    error_message = "Output private_ip_address does not match."
  }

  assert {
    condition     = azurerm_private_endpoint.this.subnet_id == var.subnet_id
    error_message = "Endpoint must reference the caller subnet."
  }

  assert {
    condition     = azurerm_private_endpoint.this.private_service_connection[0].name == "psc-test"
    error_message = "Service connection name must derive from the input name."
  }

  assert {
    condition     = azurerm_private_endpoint.this.private_service_connection[0].subresource_names == tolist(["blob"])
    error_message = "Service connection must select the caller subresource."
  }

  assert {
    condition     = azurerm_private_endpoint.this.private_service_connection[0].is_manual_connection == false
    error_message = "The connection must use automatic approval configuration."
  }
}

run "dns_zone_present" {
  command = plan

  variables {
    name                = "test-dns"
    resource_group_name = "rg-test"
    location            = "eastus"
    subnet_id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/subnet-test"
    target_resource_id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Storage/storageAccounts/st-test"
    subresource_name    = "blob"
    private_dns_zone_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"
  }

  assert {
    condition     = length(azurerm_private_endpoint.this.private_dns_zone_group) == 1
    error_message = "Private DNS zone group should be created when private_dns_zone_id is provided."
  }

  assert {
    condition     = azurerm_private_endpoint.this.private_dns_zone_group[0].private_dns_zone_ids[0] == var.private_dns_zone_id
    error_message = "Private DNS zone ID in the group is incorrect."
  }

  assert {
    condition     = azurerm_private_endpoint.this.private_dns_zone_group[0].name == "default"
    error_message = "DNS zone group name must be default."
  }

  assert {
    condition     = length(azurerm_private_endpoint.this.tags) == 0
    error_message = "Tags must default to an empty map."
  }
}
