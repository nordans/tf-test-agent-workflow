terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.11.0"
    }
    external = {
      source  = "hashicorp/external"
      version = "= 2.4.2"
    }
  }
}
