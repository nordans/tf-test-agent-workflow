# Solution

## Current scope

A custom Python agent analyzes one module, loads reusable skills, proposes native
Terraform tests and verifies them in Docker. Authoring currently supports only
`log-analytics-workspace` and creates one new test file. The agent produces a JSON
report and a patch; an operator reviews and applies the proposal. It has no Git
write tool. Infrastructure source is never changed by the agent.

PR checks run the reviewed tests and a retention mutation check without Gemini
or Azure credentials. Automatic PR change analysis, live agent invocation on PRs,
updating existing tests, and posting findings to a PR are **not implemented yet**.
The final example PR demonstrating the complete workflow will come later.

## Running it

Use Python 3.12, uv 0.13.6, Docker with a Linux amd64 engine, and a trusted checkout.
Install uv following its [installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
Run these commands from the repository root; uv manages the virtual environment:

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -B -m pytest
uv run --locked python -B -m test_agent.build_runtime
uv run --locked python -B -m test_agent.verify
```

Building the runtime requires network access to obtain Terraform and the provider.
Verification itself has no network access. It runs the checked-in proposal and
does not use any model API. PR checks run Ruff and pytest in `Code quality CI`.
The separate `Terraform mocked tests` workflow builds the runtime, runs
verification with runtime dependencies only, and uploads `verification.json`,
including failure results.

For live analysis, set `GEMINI_API_KEY` securely in the process environment:

```sh
uv run --locked python -B -m test_agent log-analytics-workspace
```

Do not put a literal key in shell history, committed files or reports. Live agent
commands send module source to Gemini and consume its project quota. Generation
adds `--generate` and requires the runtime to be built first. It writes
`generated.tftest.hcl`, `proposal.patch` and `report.json` under the ignored
`.agent-output/log-analytics-workspace/` directory. Existing artifacts are never
overwritten. To reproduce the initial generation, use a disposable checkout
without `modules/log-analytics-workspace/tests/generated.tftest.hcl` and without
previous artifacts: this stage intentionally refuses to update an existing file.

## Agent, skills and decisions

- `agent/instructions.md` and `test_agent/planner.py` define the planner. Its tools
  are `load_skill`, `read_module` and `submit_test_plan`. Pydantic checks the plan
  and evidence must reference supplied source lines.
- `skills/terraform-change-analysis/SKILL.md` provides the analysis procedure.
  Skills advertise their description first; full instructions are returned only
  when the model calls `load_skill`.
- `agent/test-writer.md` and `test_agent/author.py` define authoring. Its tools are
  `load_skill`, `propose_test_file`, `verify_module` and `submit_result`.
  `skills/terraform-unit-testing/SKILL.md` teaches mocked plan tests, concrete
  expectations, known computed IDs and interpretation of verification failures.
- Python controls tool availability, paths, proposal budgets, HCL acceptance,
  execution and final status. The model supplies HCL content, never commands,
  arbitrary paths or execution claims. One verification can be followed by one
  repair, with at most two proposals including rejected ones. An ordinary failed
  assertion stops automatic repair and sets `requires_review`. Accepted assertion
  conditions, run identity, inputs and mocks must be preserved during repairs.
- The planner has at most three API requests and the author at most six. Each
  request has a 4096 output-token cap and a 60-second socket timeout. There are no
  automatic API retries. The report records actual token usage.

Python and a small Gemini REST adapter keep the tool loop inspectable, at the
cost of maintaining protocol handling. Pydantic validates plans, PyYAML reads
skill metadata, and python-hcl2 parses HCL. Direct dependencies are declared in
`pyproject.toml`; `uv.lock` records their resolved dependencies and distribution
hashes. It replaces the previous hand-maintained `requirements.txt`. uv checks
downloaded packages against the recorded hashes; this verifies integrity, not
whether a package is trustworthy or free of vulnerabilities. `--locked` refuses
to change an absent or outdated lock file. Dependency updates are explicit; an
automated dependency update process is not configured yet.

The supported Python minor version is 3.12, recorded in `.python-version` and
restricted in project metadata; other versions are not tested. uv is pinned by
`tool.uv.required-version`, which the SHA-pinned setup action also reads. The
project runs from the checkout without a packaging/build backend. The `dev` group
contains Ruff and pytest. The Python PR job runs lint, format checks and pytest;
existing `unittest.TestCase` tests run unchanged under pytest. Ruff enables a small
explicit rule set for errors, imports, common pitfalls and Python 3.12 syntax.
The Terraform job uses `--no-dev` for both sync and run, so it installs only runtime
dependencies. The manual Gemini smoke test uses the standard library and needs
neither project dependencies nor uv.

## CI security checks

All automatic checks run on opened, updated or reopened PRs targeting `main`,
without path filters. No Gemini or Azure credentials are supplied. The checks are:

| Check | Gate |
| --- | --- |
| Code quality CI | Ruff lint/format and pytest must pass. |
| Workflow validation | Actionlint and ShellCheck must accept the workflows/installer. |
| Secret scan | Betterleaks 1.9.0 scans all fetched Git history; findings fail the job. |
| Dependency audit | pip-audit 2.10.1 fails on known Python vulnerabilities or incomplete auditing. |
| Terraform CI | Mock tests/mutation must pass and Trivy must find no HIGH/CRITICAL image vulnerabilities. |

The secret scan checks committed content, including the PR commits, with full
history, 100% redaction and inline allow comments disabled. It never enables live
credential validation and does not publish a secret report as an artifact. It
cannot prevent a secret from first being pushed; local scanning before a commit
is still useful. It does not inspect arbitrary untracked files on a developer's
machine.

The separate `audit` dependency group pins pip-audit and its dependencies in
`uv.lock`. CI exports all dependency groups with hashes and audits their pinned
versions without invoking pip resolution or installation. Platform markers apply
to the Linux CI environment. PyPI supplies advisory data; network/scanner errors
fail the check. JSON results are retained for seven days. No automatic dependency
fixes or vulnerability suppressions are configured.

Trivy 0.76.1 scans the built runner image, including OS packages and embedded Go
binaries, and saves the full JSON report. A separate `trivy convert` gate reads
that report and fails on HIGH or CRITICAL findings, including unfixed issues.
There are no ignore lists. Scanner/database errors also fail the job. Reports are
uploaded with test verification for seven days. The database is refreshed during
scanning, so identical code can fail later when new advisories appear. Scanning
requires network access; Terraform execution remains networkless.

`.github/scripts/install-security-tool.sh` downloads fixed Linux amd64 releases
of Actionlint, Betterleaks and Trivy, verifies committed SHA-256 archive hashes,
then extracts only the selected binary. This avoids running a downloaded install
script. Checksums pin reviewed downloads; they do not prove software is harmless.
GitHub actions are also pinned by commit SHA. Security checks can be dispatched
manually; image scanning uses the Terraform workflow's manual dispatch.

The initial scan found HIGH/CRITICAL advisories in AzureRM 4.81.0's embedded Go
dependencies. That is the last 4.x release available at the time of review.
The test runtime now pins AzureRM 5.11.0; scanning that image found no HIGH or
CRITICAL vulnerabilities with the database used during review. Four findings with
UNKNOWN severity remain in the full report and need manual triage; this gate does
not block unclassified findings. Findings are never used as a baseline to silence
scans. The active GitHub `main` ruleset requires all five checks above, each from
the GitHub Actions app. PRs are required, force pushes/deletion are blocked and
the ruleset has no bypass actors. Requiring an up-to-date branch remains disabled.
These settings are maintained in GitHub, not enabled by workflow files themselves;
copying the repository does not copy its protection settings. The manual Gemini
connectivity check is not required because it does not run on PRs.

Only `log-analytics-workspace` broadens its provider constraint to `>= 4.0, < 6.0`.
Local mocked tests and the retention mutation pass with both 4.81.0 and 5.11.0.
This permits the runner to use 5.x while existing stack consumers still select
4.x through their other constraints. Other modules and the stack are not migrated
to 5.x, and their 4.x provider dependency still needs separate security review.
These checks do not prove live Azure compatibility across either major version
or test every version allowed by the constraint.

## Execution and trust boundaries

`context.py` snapshots only top-level `.tf` files and direct `tests/*.tftest.hcl`
files, with size/count limits and rejection of links and paths outside the module.
`hcl_policy.py` accepts a narrow source and test structure: the known workspace
resource, approved AzureRM provider, explicit `command = plan`, mocked provider,
literal inputs and assertions. External sources, data blocks, real provider
configuration, backend/cloud blocks, provisioners and test overrides are rejected.
Only the computed resource ID may be mocked; retention, tags and SKU stay real
Terraform expressions. This is a structural allowlist, not proof of test quality.

`runtime/Dockerfile` pins Terraform 1.17.1 by digest and AzureRM 5.11.0 by version
and committed lock hashes generated by `terraform providers lock` from HashiCorp's
signed checksums. The unpacked provider is moved into the image mirror without
retaining a second copy. Build-time init
checks the provider package against the lock and prepares an unpacked mirror.
Runtime init uses only that mirror, with no direct download fallback, backend
disabled and a read-only lock file. Python resolves the built image to its
immutable local image ID before running it. Image build reproducibility and
registry provenance would need stronger release controls for production.

`sandbox.py` launches a non-root container with no network, read-only root/input,
all capabilities dropped, no new privileges, and limits on CPU, memory, processes,
output and wall time. Writable directories are temporary `noexec` filesystems.
The Docker CLI receives an environment allowlist; Gemini, GitHub and Azure secrets
are excluded, and no environment is forwarded into the container. Only a temporary
source snapshot is mounted; the repository, Docker socket and host home are not.
The trusted shell script runs fixed `fmt`, `init`, `validate` and `test -json`
commands. Python requires completed named runs and a nonempty passing summary;
exit code zero alone is insufficient. Container cleanup also runs on timeout.

Policy text comes from `trusted_root`, separately from source data. Local commands
use the current checkout for both, so run them only when you trust its code.
This PR's CI is ordinary credential-free test CI, not a privileged agent workflow.
The future agent workflow must load code/policies from the trusted base and keep
PR source, diagnostics and model content as untrusted data. Containers share the
host kernel; these controls are not a complete boundary against hostile native
code. Do not execute this runner against arbitrary repositories on a sensitive
self-hosted machine.

## Recorded run and checks

`docs/examples/log-analytics-workspace.plan.json` records the earlier planner run.
`docs/examples/log-analytics-workspace.tests.json` records a real Gemini generation
before adding the proposal to the module, using the earlier 4.81.0 runtime. The
same reviewed HCL also passes with the current 5.11.0 runtime. The author loaded
the testing skill,
proposed HCL, invoked verification and submitted the result. Its first proposal
passed: three runs, six assertions, 12694 tokens across analysis and authoring.
The operator reviewed and copied the exact verified proposal into the module.
Hashes identify source, policies and formatted proposal; they are not signatures.

Tests check default retention 30, SKU PerGB2018, empty default tags, custom
retention 90, a custom tag and the output's reference to the mocked resource ID.
In a separate temporary source copy the verifier replaces retention variable
wiring with constant 30. The custom-retention assertion must then fail; a provider
crash does not count as detection. In the recorded run the mutant had two passing
runs and one failed assertion, so this negative control succeeded.

An earlier development run exposed a model removing a failed empty-tags assertion
to obtain green results. Human review rejected that proposal. The controller now
stops on ordinary assertion failures and prevents removal or alteration of accepted
conditions and their inputs during repair; the skill uses `length(tags) == 0`
to avoid empty object/map equality differences. Offline tests cover these guards,
tool gating, HCL rejection, execution flags, secret exclusion and result parsing.

## Limits and next steps

Mock tests exercise Terraform defaults, expressions, wiring and output references.
They do not prove Azure API behavior, permissions, deployment success, networking
or service-side constraints. A mocked ID proves output wiring, not Azure identity.
One retention mutation is useful evidence, not a coverage metric or general proof
that assertions are meaningful. Plan evidence can be correctly located yet wrong.
Human review remains necessary, including after a passing verification.

The source allowlist is deliberately module-specific and rejects unsupported
constructs rather than broadening permissions. Existing unrelated test files are
visible to the planner but are not executed by this proposal verifier. Other
modules, existing-test updates, PR diffs and PR reporting remain future stages.
With more modules and teams, use impact selection, per-module budgets, maintained
provider runtimes, concurrent isolated jobs and explicit policy ownership.
Production work also needs abuse/cost controls for public PRs, trusted artifact
handoff tied to an exact PR revision, a stronger execution boundary, model
evaluations, data-handling rules, dependency verification and operational metrics.
