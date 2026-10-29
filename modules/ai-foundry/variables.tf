variable "name" {
  description = "Name of the Microsoft Foundry account."
  type        = string

  validation {
    condition     = length(trimspace(var.name)) > 0
    error_message = "The account name must not be blank."
  }
}

variable "resource_group_name" {
  description = "Existing resource group for the account."
  type        = string
}

variable "location" {
  description = "Azure region for the account."
  type        = string
}

variable "custom_subdomain_name" {
  description = "Custom subdomain used for identity-based authentication. Global availability is not checked by this module."
  type        = string

  validation {
    condition     = length(trimspace(var.custom_subdomain_name)) > 0
    error_message = "The custom subdomain must not be blank."
  }
}

variable "tags" {
  description = "Caller tags. The module owns the managed_by tag."
  type        = map(string)
  default     = {}
}
