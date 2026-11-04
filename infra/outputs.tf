output "resource_group_name" {
  value = azurerm_resource_group.demo.name
}

output "gateway_id" {
  value = azapi_resource.gateway.id
}

output "gateway_principal_id" {
  value = azapi_resource.gateway.identity[0].principal_id
}

output "gateway_hostname" {
  value = azapi_resource.gateway.output.properties.frontend.defaultHostname
}
