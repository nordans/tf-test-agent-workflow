mock_provider "azurerm" {
  override_during = plan
}

run "empty_subnets_create_no_subnet_resources" {
  command = plan

  variables {
    name                = "empty"
    resource_group_name = "rg-empty"
    location            = "westeurope"
    address_space       = ["10.50.0.0/16"]
    subnets             = {}
  }

  assert {
    condition     = length(azurerm_subnet.this) == 0
    error_message = "An empty input map must create no subnets."
  }

  assert {
    condition     = length(azurerm_network_security_group.this) == 0
    error_message = "An empty input map must create no network security groups."
  }

  assert {
    condition     = length(azurerm_subnet_network_security_group_association.this) == 0
    error_message = "An empty input map must create no associations."
  }

  assert {
    condition     = length(output.subnet_ids) == 0
    error_message = "An empty input map must expose an empty subnet output map."
  }

  assert {
    condition = (
      azurerm_virtual_network.this.name == "vnet-empty" &&
      toset(azurerm_virtual_network.this.address_space) == toset(["10.50.0.0/16"])
    )
    error_message = "The virtual network must still be planned when no subnets are requested."
  }
}
