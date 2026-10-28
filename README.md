# Terraform Platform Base

A small Terraform repository for a data platform on Azure: networking, storage, secrets, monitoring, data integration, analytics and a function app.

The starting Terraform code comes from [cf-recruitment/tf-platform-base](https://github.com/cf-recruitment/tf-platform-base).
This repository adds a Terraform test agent; see [SOLUTION.md](SOLUTION.md) for the
current scope, prerequisites, usage, and limitations.

## PR test agent

After installation on main, the PR test agent analyzes changes to
log-analytics-workspace in same-repository PRs. Configure the GEMINI_API_KEY
repository secret. The workflow posts a report with the analyzed revisions,
findings and test results. Download the pr-result artifact to review the verified
proposal.patch and apply it to the same PR. The agent does not commit changes.

To rerun, select PR test agent in GitHub Actions, choose main and enter the open
PR number. Fork, bot and draft PRs do not invoke Gemini. Other modules and changes
to agent policies are reported as unsupported or requiring review.

For local execution, install Betterleaks 1.9.0, build the Docker runtime, securely set GEMINI_API_KEY and
provide full Git commit IDs:

```sh
uv run --locked python -B -m test_agent.build_runtime
uv run --locked python -B -m test_agent.pr_workflow local --repo /path/to/checkout --base BASE_SHA --head HEAD_SHA --output .agent-output/pr-run
```

The output directory must be new. Review report.json, proposal.patch and comment.md.
Local execution scans the selected source before contacting Gemini and never
publishes a GitHub comment. If Betterleaks is not on PATH, pass --secret-scanner
with its executable path.

## Python development

Use Python 3.12 and uv 0.13.6. From the repository root:

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -B -m pytest
uv run --locked python -B -m test_agent.build_runtime
uv run --locked python -B -m test_agent.check_modules
```

These same checks run on pull requests. To apply formatting locally, run
`uv run --locked ruff format .`. Commit dependency changes in `pyproject.toml`
together with the generated `uv.lock`; use `uv add` or `uv add --dev` to add a
dependency. See [SOLUTION.md](SOLUTION.md) for agent and Docker commands.

PRs also run workflow validation, secret and dependency scans, and a vulnerability
scan of the Terraform runner image. See [CI security checks](SOLUTION.md#ci-security-checks)
for the exact gates, reports and current provider findings.

The module compatibility check runs offline init and validation for all modules
and stacks against the pinned provider schemas in Docker. It does not run plans,
execute external data-source programs, or prove live Azure compatibility.

## Layout

```text
modules/            Reusable modules. Each has main.tf, variables.tf, outputs.tf and versions.tf.
  <module>/tests/   Tests for a module, where they exist. Run them from the module folder.
stacks/
  data-platform/    The stack that uses the modules. One set of variables per environment in envs/.
```

## Requirements

- Terraform 1.9 or newer
- Internet access to download providers
- No Azure subscription or credentials

## Run the tests of a module

The tests use a mocked `azurerm` provider, so they need no credentials.

```sh
cd modules/storage-account
terraform init -backend=false
terraform test
```

## Check the stack

```sh
cd stacks/data-platform
terraform init -backend=false
terraform fmt -check -recursive ../..
terraform validate
```

The stack has no backend configured. A real deployment would need one, and credentials for it.

## Conventions

- Resource files in a stack and modules use `main.tf`, `variables.tf`, `outputs.tf` and `versions.tf`.
- Use `for_each` rather than `count` for repeated resources.
- Environments are `dev`, `uat` and `prod`.
- Format code with `terraform fmt -recursive` before committing.
