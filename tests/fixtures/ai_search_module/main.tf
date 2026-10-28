terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.0"
    }
  }
}

variable "name" {
  type = string
}

variable "sku" {
  type    = string
  default = "basic"
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "azurerm_search_service" "this" {
  name                          = var.name
  resource_group_name           = "rg-test"
  location                      = "westeurope"
  sku                           = var.sku
  public_network_access_enabled = false
  tags                          = var.tags
}

output "id" {
  value = azurerm_search_service.this.id
}
