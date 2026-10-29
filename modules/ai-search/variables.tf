variable "name" {
  description = "Search service name."
  type        = string

  validation {
    condition     = length(trimspace(var.name)) > 0
    error_message = "Search service name must not be blank."
  }
}

variable "resource_group_name" {
  description = "Existing resource group for the search service."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "sku" {
  description = "Supported search service tier."
  type        = string
  default     = "basic"

  validation {
    condition     = contains(["basic", "standard"], var.sku)
    error_message = "SKU must be basic or standard."
  }
}

variable "replica_count" {
  description = "Number of search replicas."
  type        = number
  default     = 1

  validation {
    condition     = var.replica_count >= 1 && var.replica_count <= 3 && floor(var.replica_count) == var.replica_count
    error_message = "Replica count must be an integer between 1 and 3."
  }
}

variable "tags" {
  description = "Tags passed to the search service."
  type        = map(string)
  default     = {}
}
