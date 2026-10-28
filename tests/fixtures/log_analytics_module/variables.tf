variable "name" {
  description = "Workspace name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the workspace."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "retention_in_days" {
  description = "How long logs are kept."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}
