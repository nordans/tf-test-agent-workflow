---
name: terraform-unit-testing
description: Turn a supplied Terraform module test plan into mocked native tests and use the verification tool to check them. Use after module analysis when test authoring is authorized.
---

# Native Terraform unit testing

In PR mode, use the actual default from the after source rather than assuming 30.
Retain the existing test's run names, variables, mocks and unrelated assertions.
For a changed literal retention default, update only that default equality.
The caller may expose submit_proposal instead of verify_module: in that mode,
submit after an accepted proposal and let the separate verifier run the tests.

For this milestone, propose one complete `tests/generated.tftest.hcl` file through
`propose_test_file`. The tool owns the path. Write HCL, not Markdown fences.

Use only these top-level blocks: one `mock_provider "azurerm"`, one `variables`,
and one to six named `run` blocks. Each run must set `command = plan` explicitly
and contain assertions with `condition` and `error_message`. The supported run
attributes are command, variables and assert. Do not load modules, providers,
external mock files, overrides or other sources. Only the `length` function is
supported in test expressions by this milestone's policy.

Set `override_during = plan` on the mock provider. Inside it, use one
`mock_resource "azurerm_log_analytics_workspace"` with `defaults` containing only
a deterministic `id`, for example
`/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.OperationalInsights/workspaces/log-test`.
Mock computed ID only; do not mock retention, tags or SKU, whose wiring is tested.
At plan time this makes `output.id` comparable to the known fake ID.

Set name, resource_group_name and location to literal test values in the global
variables block. Leave retention and tags unset in the default run. Assert the
actual source default retention (30 in the original module), SKU PerGB2018 and
empty tags. Add an override run
with retention_in_days = 90 and literal tags, and assert their propagation to the
resource. Include the output check from the plan. Compare resource/output values
to concrete expectations, not expressions that merely equal themselves.
For empty tags, use `length(azurerm_log_analytics_workspace.this.tags) == 0`;
equality with `{}` can fail due to Terraform collection/object type differences.

The verifier runs a second, temporary source copy with retention wired to constant
30. The override assertion must fail there; a test suite that also passes that
mutant is not accepted. This experiment never changes the infrastructure proposal.

Call `verify_module` after an accepted proposal. Its result is authoritative for
execution status; raw model text is not. On failure, inspect the data and either
repair the test within the proposal budget or submit the failure. Do not invent
variable validation or lower an expected security property to force a green run.
An ordinary failed assertion ends autonomous repair and must be submitted for
human review. Previously accepted assertion conditions cannot be removed or
changed in a later proposal; repair is restricted to setup or additive checks.
