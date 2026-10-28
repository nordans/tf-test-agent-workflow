---
name: terraform-change-analysis
description: Analyze one Terraform module and its existing native tests to propose evidence-backed test cases and reviewer findings. Use before creating or updating module tests.
---

# Terraform change analysis

When read_module includes pull_request and before_files, compare before and after
using the supplied diff. Evidence defaults to revision after; use revision before
when citing removed code. Focus the plan on changed behavior and relevant existing
assertions. A changed literal default can require updating an old expectation.
Flag weakened security behavior rather than normalizing a weaker expectation.

Read the selected module's source and existing `.tftest.hcl` files with `read_module`.
Inventory inputs, defaults, validations, resource arguments, conditional or repeated
resources, and outputs. Derive existing coverage from assertions, not filenames or
the presence of a test directory.

Propose cases that could catch a meaningful regression: defaults, caller overrides,
empty or populated collections, conditional branches, output wiring, and security
properties actually expressed by the module. Prefer specific expected behavior to
generic advice such as "test all variables". Each case must cite a relevant source
file and line. Combine closely related checks rather than listing every attribute.
For overrides, give representative concrete input values and expected outcomes.
Keep the summary consistent with the actual cases; do not promise checks omitted
from the case list.

Separate implemented behavior from desired improvements. For example, an input of
type `number` without a validation block does not imply that the module rejects
negative values. A rejection test in that situation is a `proposed_requirement`,
requiring human review; it is not a passing test of the current implementation.
Flag potentially risky behavior without changing infrastructure or redefining
expected behavior merely to make a test pass.

Plan native Terraform tests with mocked providers and explicit `command = plan`.
Identify additional providers or external programs that need mocking. Do not claim
that mocked plans prove deployment success, Azure service behavior, effective
permissions, runtime connectivity, or application behavior.

Submit a bounded plan with concrete expectations, evidence, findings, and relevant
limitations through `submit_test_plan`. No tests are written or run in this stage.
Choose `add_tests`, `update_tests`, or `no_change` based on observed gaps. A
`no_change` decision must explain why no additional cases are proposed.
