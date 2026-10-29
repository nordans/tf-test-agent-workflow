# Terraform Test Agent

A Python agent that proposes and verifies native Terraform tests for GitHub pull
requests. It discovers changed modules by path, including new modules, and reports
a separate result for each module. Module names and AzureRM resource types do not
need registration in the agent.

The Terraform platform code starts from
[cf-recruitment/tf-platform-base](https://github.com/cf-recruitment/tf-platform-base).
It contains Azure modules and a data-platform stack. Tests use mocked providers
and require no Azure subscription or credentials.

## GitHub workflow

Configure the GEMINI_API_KEY repository secret and install the workflows on main.
The PR test agent runs automatically for same-repository PRs targeting main:

1. Read immutable before/after module snapshots and scan their source for secrets.
2. Load analysis and testing skills, then propose only allowed test-file changes.
3. Validate HCL against approved provider schemas and run complete module suites
   in isolated containers, including bounded source-mutation controls.
4. Update one bot comment on the originating PR with decisions, findings and results.

Download the pr-result artifact, review proposal.patch and apply it to that same
PR. Repeated runs recognize tests that are already current. The agent does not
commit infrastructure or tests, open another PR, or merge changes.

To rerun manually, select PR test agent in Actions, choose main and enter the PR
number. Fork, bot and draft PRs do not call Gemini. Changes to the controller,
skills, runtime or workflow require human review.

## Supported inputs

Modules live directly under modules/<name>/, with top-level .tf files and direct
tests/*.tftest.hcl files. Up to eight changed modules are processed per run.
AzureRM and the mocked external provider are available in the pinned runtime.
New resource types work when supported by that runtime's provider schema.

Remote/nested modules, provider aliases, other providers, backend/cloud
configuration, filesystem/execution functions and provisioners are outside this
execution policy. Unsupported modules receive a reason requiring review;
supported modules in the same PR can still produce verified patches.
Stack impact analysis and changes to external programs require human review.
Stack/root impact warnings do not prevent testing independent changed modules;
the overall report remains needs_review.
Script text can be read as source data; it is never executed.

Unchanged module .terraform.lock.hcl files are excluded from model/execution
snapshots. Tests use the approved runtime's provider locks. Added, modified,
renamed or deleted module locks require review for that module; other supported
modules can still be processed. Linked, executable and oversized locks are rejected.

Existing test files, scenarios, inputs, mock values and unrelated assertions are
preserved. Source-backed scalar equality updates are supported; Boolean
expectation changes require review. Every provider must be mocked and every run
must explicitly use plan.

## Local execution

Requirements: Python 3.12, uv 0.13.6, an amd64 Linux Docker engine, Betterleaks
1.9.0, Git and a Gemini API key. Building the runtime needs network access;
Terraform tests run without network access.

~~~sh
uv sync --locked
uv run --locked python -B -m test_agent.build_runtime
~~~

Set GEMINI_API_KEY securely in the process environment, then supply full Git
commit IDs from a trusted local checkout:

~~~sh
uv run --locked python -B -m test_agent.pr_workflow local \
  --repo /path/to/checkout --base BASE_SHA --head HEAD_SHA \
  --output .agent-output/pr-run
~~~

Use a new output directory for each run. If Betterleaks is outside PATH, pass
--secret-scanner with its executable path. The command scans source before any
model call and saves context.json, draft.json, report.json, proposal.patch and
comment.md. Local execution never publishes a GitHub comment. Module source is
sent to Gemini and consumes the API project's quota.

## Development and CI

~~~sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -B -m pytest
uv run --locked python -B -m test_agent.verify_repository
uv run --locked python -B -m test_agent.check_modules
~~~

Code quality CI runs Ruff and pytest. Security checks validate workflows and shell,
scan Git history for secrets, and audit locked Python dependencies. Terraform CI
runs all checked-in module suites, validates every module and stack offline, and
scans the final runtime image. HIGH/CRITICAL image findings fail the check.

Changed modules require checked-in tests and detection of a source mutation.
Missing tests and undetected sampled mutations in unchanged modules remain
visible in verification.json. All eight modules have test suites. The existing
network suite still misses the sampled mutations; a passing CI result does not
claim complete behavioral coverage.
The terraform-verification artifact contains test, compatibility and image reports.

The original standalone module planner remains available with
python -m test_agent <module>. Its older --generate bootstrap is limited to the
workspace module; use the Git-based command above for general test authoring.

See [SOLUTION.md](SOLUTION.md) for architecture, budgets, examples and limitations,
and [the external build notes](runtime/external-build/README.md) for maintenance of
the rebuilt external provider.

## Repository layout

~~~text
agent/                   Trusted agent instructions
skills/                  Reusable analysis and testing skills
test_agent/              Python controller, policies and runners
runtime/                 Pinned Terraform/providers and fixed sandbox scripts
modules/                 Terraform modules and their native tests
stacks/data-platform/    Environment stack
tests/                   Offline controller tests and synthetic module fixtures
docs/examples/           Recorded example results
~~~
