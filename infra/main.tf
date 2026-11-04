terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id
  features {}
}

provider "azapi" {
  subscription_id = var.subscription_id
}

resource "azurerm_resource_group" "demo" {
  name     = var.resource_group_name
  location = var.location
}

locals {
  gateway_name = coalesce(var.gateway_name, "aigw-${substr(md5("${var.subscription_id}/${var.resource_group_name}"), 0, 12)}")
}

resource "azapi_resource" "gateway" {
  type      = "Microsoft.ApiManagement/aigateways@2026-09-01-preview"
  name      = local.gateway_name
  parent_id = azurerm_resource_group.demo.id
  location  = var.location

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {}
  }

  response_export_values    = ["properties.frontend.defaultHostname"]
  schema_validation_enabled = false
}
