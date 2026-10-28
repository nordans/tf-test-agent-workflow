mock_provider "azurerm" {
  override_during = plan

  # IDs are computed mocks shared per resource type, not configurable inputs.
  mock_resource "azurerm_virtual_network" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.Network/virtualNetworks/vnet-mock"
    }
  }
  mock_resource "azurerm_subnet" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.Network/virtualNetworks/vnet-mock/subnets/snet-mock"
    }
  }
  mock_resource "azurerm_network_security_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.Network/networkSecurityGroups/nsg-mock"
    }
  }
}

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

run "caller_overrides_are_forwarded" {
  command = plan

  variables {
    name                = "custom"
    resource_group_name = "rg-custom"
    location            = "northeurope"
    address_space       = ["10.42.0.0/16", "10.43.0.0/16"]
    subnets = {
      api = {
        address_prefixes  = ["10.42.1.0/24"]
        service_endpoints = ["Microsoft.Storage", "Microsoft.KeyVault"]
      }
      batch = {
        address_prefixes  = ["10.42.2.0/24"]
        service_endpoints = []
      }
    }
    tags = {
      environment = "test"
      owner       = "platform"
    }
  }

  assert {
    condition     = azurerm_virtual_network.this.name == "vnet-custom"
    error_message = "The virtual network name must use the caller name."
  }

  assert {
    condition = (
      azurerm_virtual_network.this.resource_group_name == var.resource_group_name &&
      azurerm_virtual_network.this.location == var.location
    )
    error_message = "The virtual network must use the caller resource group and location."
  }

  assert {
    condition     = toset(azurerm_virtual_network.this.address_space) == toset(var.address_space)
    error_message = "Every caller address range must reach the virtual network."
  }

  assert {
    condition     = azurerm_virtual_network.this.tags == var.tags
    error_message = "All caller tags must reach the virtual network."
  }

  assert {
    condition = alltrue([
      for key, subnet in azurerm_subnet.this :
      toset(subnet.address_prefixes) == toset(var.subnets[key].address_prefixes)
    ])
    error_message = "Subnet address prefixes must come from the matching input key."
  }

  assert {
    condition = alltrue([
      for subnet in azurerm_subnet.this :
      subnet.resource_group_name == var.resource_group_name &&
      subnet.virtual_network_name == azurerm_virtual_network.this.name
    ])
    error_message = "Every subnet must belong to the caller resource group and this virtual network."
  }

  assert {
    condition = alltrue([
      for key, subnet in azurerm_subnet.this :
      length(var.subnets[key].service_endpoints) == 0 ? length(subnet.service_endpoint) == 0 :
      toset([for endpoint in subnet.service_endpoint : endpoint.service]) == toset(var.subnets[key].service_endpoints)
    ])
    error_message = "Subnet service endpoints must preserve each caller list, including the empty list."
  }

  assert {
    condition = alltrue([
      for key, nsg in azurerm_network_security_group.this :
      nsg.name == format("nsg-custom-%s", key)
    ])
    error_message = "Each network security group name must use the caller name and its subnet key."
  }

  assert {
    condition     = alltrue([for nsg in azurerm_network_security_group.this : nsg.tags == var.tags])
    error_message = "All caller tags must reach every network security group."
  }

  assert {
    condition = alltrue([
      for nsg in azurerm_network_security_group.this :
      nsg.resource_group_name == var.resource_group_name && nsg.location == var.location
    ])
    error_message = "Every network security group must use the caller resource group and location."
  }

  assert {
    condition = (
      toset(keys(azurerm_subnet.this)) == toset(keys(var.subnets)) &&
      toset(keys(azurerm_network_security_group.this)) == toset(keys(var.subnets)) &&
      toset(keys(azurerm_subnet_network_security_group_association.this)) == toset(keys(var.subnets))
    )
    error_message = "Subnets, network security groups and associations must preserve the caller keys."
  }

  assert {
    condition = alltrue([
      for key, association in azurerm_subnet_network_security_group_association.this :
      association.subnet_id == azurerm_subnet.this[key].id &&
      association.network_security_group_id == azurerm_network_security_group.this[key].id
    ])
    error_message = "Each association must connect the subnet and network security group at the same key."
  }
}

run "additional_subnet_gets_resources" {
  command = plan

  variables {
    subnets = {
      apps = {
        address_prefixes  = ["10.10.0.0/24"]
        service_endpoints = ["Microsoft.Storage"]
      }
      data = {
        address_prefixes = ["10.10.1.0/24"]
      }
      ops = {
        address_prefixes = ["10.10.2.0/24"]
      }
    }
  }

  assert {
    condition     = toset(keys(azurerm_subnet.this)) == toset(["apps", "data", "ops"])
    error_message = "A third input key must create a third subnet."
  }

  assert {
    condition     = toset(keys(azurerm_network_security_group.this)) == toset(["apps", "data", "ops"])
    error_message = "A third input key must create a third network security group."
  }

  assert {
    condition     = toset(keys(azurerm_subnet_network_security_group_association.this)) == toset(["apps", "data", "ops"])
    error_message = "A third input key must create a third association."
  }

  assert {
    condition     = toset(keys(output.subnet_ids)) == toset(["apps", "data", "ops"])
    error_message = "The subnet output must include every caller key."
  }
}

run "outputs_preserve_resource_ids" {
  command = plan

  assert {
    condition     = output.vnet_id == azurerm_virtual_network.this.id
    error_message = "The virtual network output must expose its resource ID."
  }

  assert {
    condition     = toset(keys(output.subnet_ids)) == toset(keys(var.subnets))
    error_message = "The subnet output must preserve the caller keys."
  }

  assert {
    condition     = alltrue([for key, id in output.subnet_ids : id == azurerm_subnet.this[key].id])
    error_message = "Each subnet output value must reference the matching resource ID."
  }
}
