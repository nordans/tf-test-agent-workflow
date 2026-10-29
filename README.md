# Terraform Test Agent

A Python agent that proposes and verifies native Terraform tests for changed
Azure modules in a GitHub pull request. It uses reusable analysis/authoring skills,
approved provider schemas and isolated execution. Results and a test-only patch
are published to the originating PR for review.

[Example PR](https://github.com/nordans/tf-test-agent-workflow/pull/20) shows a new
Foundry module and an existing retention change, from missing/stale tests through
generation, execution and verification. Its infrastructure changes remain outside
main. [Saved evidence](docs/examples/final-pr.json) remains available after Actions
artifacts expire.

## Requirements

- Python 3.12, uv 0.13.6 and Git.
- An amd64 Linux Docker engine for Terraform checks.
- Betterleaks 1.9.0 on PATH, or its executable path for local agent runs.
- GEMINI_API_KEY for live model calls; no Azure subscription or credentials.

Runtime builds and scanner databases need internet access. Terraform verification
runs without network access. The repository includes eight Azure platform modules
and a stack as test targets, starting from
[tf-platform-base](https://github.com/cf-recruitment/tf-platform-base).

## Local checks

~~~sh
uv sync --locked
uv run --locked python -B -m test_agent.build_runtime
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -B -m pytest
uv run --locked python -B -m test_agent.verify_repository
uv run --locked python -B -m test_agent.check_modules
~~~

For model-assisted authoring, set GEMINI_API_KEY securely in the process
environment and supply full commit IDs from a trusted checkout:

~~~sh
uv run --locked python -B -m test_agent.pr_workflow local \
  --repo /path/to/checkout --base BASE_SHA --head HEAD_SHA \
  --output .agent-output/new-run
~~~

Use a new output directory. Add --secret-scanner /path/to/betterleaks if needed.
The command scans source before model calls and saves context.json, draft.json,
report.json, proposal.patch and comment.md. Local execution never posts to GitHub.
Selected source is sent to Gemini and consumes the API project's quota.

## GitHub use

Set the GEMINI_API_KEY repository secret and install the workflows on main.
PR test agent runs automatically for same-repository, non-draft, non-bot PRs to
main. Fork PRs do not invoke the model. To rerun, use Actions, select PR test agent
on main and supply the open PR number.

The agent reads immutable before/after source, loads its skills, proposes allowed
test files and independently verifies complete module suites plus source mutations.
It updates one marked bot comment. The pr-result artifact contains the report and
patch; artifacts expire after seven days.

Compare the comment's head SHA with your branch, inspect the patch and run
git apply --check before applying it. Commit reviewed tests to that same PR and
push to rerun. The agent has no branch-write, infrastructure-edit or merge tool.
Findings and needs_review results require human assessment.

## Supported scope

Up to eight changed leaf modules under modules/<name>/ are processed independently.
Sources are top-level .tf files and direct tests/*.tftest.hcl files. AzureRM and
mocked external are available in the pinned runtime; new resource types work when
supported by its schema. Every provider must be mocked and every run must plan.

Remote/nested modules, provider aliases, other providers, backend/cloud settings,
provisioners and filesystem/execution functions require review. Stack/root impact
warnings do not prevent independent leaf-module test proposals. Script changes
require review; script text is never executed. Unchanged module lockfiles are
excluded from snapshots, and lockfile changes require review for that module.

Existing scenarios, inputs, mocks and unrelated assertions are preserved. Supported
scalar expectation updates must follow the source change; Boolean rewrites require
review. Changed controller, skills, runtime or workflow code is not executed by
the privileged PR agent before merge.

## Continuous integration

Required checks are Code quality CI, Workflow validation, Secret scan, Dependency
audit and Terraform CI. They cover Ruff/pytest, workflow/shell validation, Git
history secret scanning, locked dependencies, all native suites, offline module
and stack validation, and the runtime image. HIGH/CRITICAL image findings fail CI.

All eight main-branch modules have suites with 26 native scenarios at the recorded
checkpoint. Changed modules require checked-in tests and source-mutation detection.
Test counts and one detected mutation do not establish complete coverage or live
Azure behavior. See terraform-verification for native/compatibility/image reports.

See [SOLUTION.md](SOLUTION.md) for decisions, permissions, examples and limitations,
and [external build notes](runtime/external-build/README.md) for provider maintenance.
The older standalone planner remains available through python -m test_agent <module>;
its --generate bootstrap is workspace-only. Use the Git-based command above for
general authoring.
