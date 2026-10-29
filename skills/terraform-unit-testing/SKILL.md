---
name: terraform-unit-testing
description: Create or update native mocked Terraform tests from module source and an evidence-backed plan, preserving existing checks and handing proposals to the independent verifier.
---

# Native Terraform unit testing

Work on the caller-selected module, including newly added modules. Derive resource
types, inputs, defaults, validations, collections, conditional branches and outputs
from its files. A familiar module name is not required. Follow the supplied plan;
report unsupported behavior rather than inventing infrastructure requirements.

In PR mode, use propose_test_files with complete HCL for each changed allowed path.
Omitted files remain unchanged. Prefer extending an existing test or adding the
fixed generated test path. Do not duplicate existing sufficient coverage.
The standalone bootstrap may expose propose_test_file instead; use only tools
actually offered. Submit after an accepted proposal. Execution is independent.

Use only mock_provider, variables and named run blocks. Every declared provider
must be mocked, including external. All runs explicitly set command = plan.
Do not load test modules, real providers, external mocks, overrides or state.
Use literal test inputs. Safe pure expression functions are allowed; filesystem,
environment, provider-defined and execution functions are forbidden.

Use override_during = plan when computed values are needed in plan assertions.
The PR caller supplies schema-derived computed_mock_fields. Mock only these
fields, using valid deterministic IDs when downstream resources parse an ID.
For nested identity blocks, mock computed principal_id or tenant_id, never type.
For a nested block, defaults must contain one object applied to all instances,
not a list. For example, inside a schema-approved mock_resource defaults object:

~~~hcl
identity = {
  principal_id = "00000000-0000-0000-0000-000000000001"
  tenant_id    = "00000000-0000-0000-0000-000000000002"
}
~~~

Use this shape only when these fields appear in computed_mock_fields for the
actual resource. Keep configured identity.type in the module and assert it.
Do not mock configurable behavior such as SKU, tags, TLS or public network flags.
For external data, mock its computed result map including every referenced key.
Its configured program must never execute. Mocked results do not test the script.

This example shows HCL nesting only. Use the actual resource and output names
from the selected module, not the example names. mock_resource and mock_data
belong inside mock_provider; override_during belongs inside mock_provider too.

~~~hcl
mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test"
    }
  }
}

variables {
  name     = "rg-test"
  location = "westeurope"
}

run "output_wiring" {
  command = plan
  assert {
    condition     = output.id == azurerm_resource_group.this.id
    error_message = "The output must expose the resource ID."
  }
}
~~~

Assert concrete expected behavior rather than comparing a value with itself.
Cover relevant defaults and caller overrides, empty/populated collections and
conditional branches. Prefer small representative cases with a clear purpose.
Implement the planned output-wiring cases as well as resource assertions. Use
schema-approved computed mocks when an output must be known during plan.

The caller supplies required_boolean_assertions derived from source. Implement
each as a direct condition = resource.attribute == true/false assertion using the
specified implemented value. The inventory paths omit instance indices; use the
actual block's schema-appropriate indexing, or one(block) for a singleton set.
Assert nested settings as well as top-level ones. Do not satisfy these checks with
an OR, an unrelated reference, a computed mock or a tautological expression.
If needed, split default/security checks into two runs rather than dropping flags.

Turn every planned validation example into an expect_failures run with otherwise
valid inputs. A lower-bound test does not cover an upper bound or integer rule.
For trimspace/nonempty checks test both empty and whitespace strings. Include
valid numeric boundaries in passing scenarios. Reconcile actual cases with the
plan before submitting; runtime success alone does not establish that the plan
was implemented.
For empty maps use length(value) == 0 to avoid collection/object type differences.
Use length checks for empty collections generally; dynamic and typed empty sets
can compare differently. For nonempty collections, compare normalized values.
Use expect_failures only for validations actually present in the module.

The caller supplies limits. Use at most 12 assertions per run and 12 runs per file.
If related checks exceed the per-run limit, split them into additional named runs
with the same inputs. Retain planned output, security and wiring assertions during
structural correction. Do not turn a broad proposal into a smaller one just to
pass a structural limit. Missing map keys can use lookup or try with a fallback
so an actual mismatch produces a clear assertion failure rather than an index error.
For example, prefer lookup(resource.tags, "Environment", "") == "test" to direct
map indexing when the missing key itself should be an assertion mismatch.

Keep existing run names, inputs, expected validation failures, mock values and
unrelated conditions. A changed scalar source default may justify updating its
old equality when the run does not override that input. Boolean expectation
changes and other unsupported rewrites require human review. Report weakened
security behavior; never weaken a check merely to make the suite pass.

The verifier runs every test file together and then tries at most three temporary
source mutations. It can freeze variable wiring to a known literal input or flip
a configured Boolean. Representative override/wiring assertions should detect
real regressions. A provider crash or invalid setup is not mutation detection.
One detected mutation is evidence of sensitivity, not a coverage percentage or
proof that all assertions are useful. Never change infrastructure to satisfy it.

Structural rejection may be corrected within the proposal budget. Runtime
assertion failures end autonomous authoring and require review; do not delete
failed checks or claim success. Mocked plans do not establish Azure API behavior,
permissions, deployment success or runtime connectivity.
