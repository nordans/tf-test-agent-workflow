# AI Search module

A small Azure AI Search service with a system-assigned identity, configurable
basic/standard SKU, one partition and an integer replica count from one to three.
Public network access and local API-key authentication are disabled. The caller
supplies an existing resource group and region. Outputs contain identifiers,
not admin or query keys.

This is an unconnected input for the open test-authoring example, not a complete
deployment. Private connectivity and data-plane role assignments are intentionally
outside this module and need infrastructure review before use. It is not wired to
the platform stack and must not be merged into main as part of the demonstration.

The PR deliberately contains no native tests for this module. The agent should
propose them as a reviewable patch and independently execute mocked plans.
Mocked plans cannot establish live access, connectivity or service availability.
