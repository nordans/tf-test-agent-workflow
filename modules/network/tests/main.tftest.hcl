mock_provider "azurerm" {}

variables {
  name                = "tp-test"
  resource_group_name = "rg-test"
  location            = "westeurope"
  address_space       = ["10.10.0.0/16"]
  subnets = {
    apps = {
      address_prefixes  = ["10.10.0.0/24"]
      service_endpoints = ["Microsoft.Storage"]
    }
    data = {
      address_prefixes = ["10.10.1.0/24"]
    }
  }
}

run "creates_one_subnet_and_nsg_per_key" {
  command = plan

  assert {
    condition     = length(azurerm_subnet.this) == 2
    error_message = "Expected one subnet for each key in var.subnets."
  }

  assert {
    condition     = length(azurerm_network_security_group.this) == 2
    error_message = "Expected one network security group for each subnet."
  }

  assert {
    condition     = length(azurerm_subnet_network_security_group_association.this) == 2
    error_message = "Every subnet must be associated with its network security group."
  }
}

run "names_follow_the_convention" {
  command = plan

  assert {
    condition     = azurerm_virtual_network.this.name == "vnet-tp-test"
    error_message = "The virtual network name must start with vnet-."
  }

  assert {
    condition     = azurerm_subnet.this["apps"].name == "snet-apps"
    error_message = "Subnet names must start with snet-."
  }

  assert {
    condition     = azurerm_subnet.this["apps"].service_endpoint[0].service == "Microsoft.Storage"
    error_message = "The service endpoint must preserve the configured service."
  }

  assert {
    condition     = length(azurerm_subnet.this["data"].service_endpoint) == 0
    error_message = "A subnet with no service endpoints must retain an empty endpoint list."
  }
}

run "rejects_invalid_address_space" {
  command = plan

  variables {
    address_space = ["not-a-cidr"]
  }

  expect_failures = [var.address_space]
}
