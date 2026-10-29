variable "name" {
  description = "AKS cluster name."
  type        = string

  validation {
    condition     = length(trimspace(var.name)) > 0
    error_message = "Cluster name must not be blank."
  }
}

variable "resource_group_name" {
  description = "Existing resource group for the cluster."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "dns_prefix" {
  description = "DNS prefix for the cluster."
  type        = string

  validation {
    condition     = length(trimspace(var.dns_prefix)) > 0
    error_message = "DNS prefix must not be blank."
  }
}

variable "tenant_id" {
  description = "Microsoft Entra tenant used for cluster authentication."
  type        = string
}

variable "node_count" {
  description = "Number of nodes in the system pool."
  type        = number
  default     = 2

  validation {
    condition     = var.node_count >= 1 && var.node_count <= 5 && floor(var.node_count) == var.node_count
    error_message = "Node count must be an integer between 1 and 5."
  }
}

variable "vm_size" {
  description = "Virtual machine size for the system pool."
  type        = string
  default     = "Standard_D2s_v5"
}

variable "tags" {
  description = "Tags passed to the cluster."
  type        = map(string)
  default     = {}
}
