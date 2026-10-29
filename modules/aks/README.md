# AKS module

A small private AKS cluster with one system node pool, a system-assigned identity,
Microsoft Entra authentication, Azure RBAC and Azure CNI overlay networking.
Local accounts, node public IPs and the public FQDN of the private API are disabled.
The caller supplies an existing resource group, region, DNS prefix and tenant ID.
The system pool defaults to two nodes; its count must be an integer from one to five.
VM size and tags are configurable. Outputs contain resource identifiers and the
private API name, not kubeconfig or credentials.

This module is an additional test-authoring input in the open example PR. It is
not connected to the platform stack and is not intended to be merged into main.
It deliberately starts without tests so the PR agent can propose them.

Mocked plan tests cannot establish region/SKU availability, live authorization,
network reachability or a complete production configuration. Deployment would need
review of networking, access assignments, upgrades, monitoring and availability.
The example workflow does not deploy a cluster or require Azure credentials.
