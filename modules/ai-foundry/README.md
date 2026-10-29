# Microsoft Foundry account

Creates a Foundry account through azurerm_cognitive_account with kind AIServices,
the S0 SKU, project management enabled and a system-assigned identity. Public
network access and local key authentication are disabled. Caller tags are merged
with a module-owned managed_by = terraform tag.

Required inputs are name, resource_group_name, location and custom_subdomain_name.
Names/subdomains must not be blank; global name availability and Azure service
acceptance are not established by these validations. tags defaults to an empty map.
Outputs expose the account ID, endpoint and identity principal ID, without keys.

The consumer must provide appropriate private connectivity and role assignments.
This module does not create projects, model deployments, private endpoints, DNS or
RBAC assignments, and does not connect to the data-platform stack.

This change is retained in the example PR for the test agent. The account is not
provisioned and the example infrastructure is not merged into main.
