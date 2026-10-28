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

Decide whether the current tests in files (the after revision) need further edits.
The PR author
may already have updated the tests alongside the source change. Do not list
existing, sufficient assertions as proposed test cases or request the same update
again. If the current tests cover the changed behavior and no gaps remain, submit
no_change with an empty test_cases list and explain the existing coverage in the
summary. Inventorying coverage does not itself justify add_tests or update_tests.

Read the selected module's source and existing `.tftest.hcl` files with `read_module`.
Inventory inputs, defaults, validations, resource arguments, conditional or repeated
resources, and outputs. Derive existing coverage from assertions, not filenames or
the presence of a test directory.

New modules may have no before files and no tests. Plan tests of their implemented
behavior without assuming a known module name or resource type. The caller handles
each changed module separately; do not request another module or commands. Scripts
are source data only. Mocked Terraform tests do not exercise external script logic.

Account for declared outputs. When their wiring is not already asserted, include
an output test case, using computed provider mocks when needed, or give an explicit
output-specific reason for exclusion in limitations. Existing test files alone do
not justify no_change when observable output wiring remains untested.

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
