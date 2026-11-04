variable "subscription_id" {
  description = "Azure subscription ID where the demo gateway will be deployed."
  type        = string
}

variable "resource_group_name" {
  description = "Name of the new resource group owned by this Terraform configuration."
  type        = string
  default     = "rg-ai-gateway-demo"
}

variable "gateway_name" {
  description = "Globally unique gateway name (lowercase letters, digits, hyphens, 3-24 characters); generated if omitted."
  type        = string
  default     = null

  validation {
    condition     = var.gateway_name == null ? true : can(regex("^[a-z0-9][a-z0-9-]{1,22}[a-z0-9]$", var.gateway_name))
    error_message = "Gateway name must be 3-24 lowercase letters, digits, or hyphens and start/end with a letter or digit."
  }
}

variable "location" {
  description = "AI Gateway public preview region."
  type        = string
  default     = "eastus2"

  validation {
    condition     = contains(["eastus2", "swedencentral"], var.location)
    error_message = "AI Gateway preview is available only in eastus2 or swedencentral."
  }
}
