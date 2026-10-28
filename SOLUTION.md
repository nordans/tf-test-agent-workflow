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

Use Python 3.12, Docker with a Linux amd64 engine, and a trusted checkout. From an
activated virtual environment at the repository root:

```sh
python -m pip install -r requirements.txt
python -B -m unittest discover -s tests -v
python -B -m test_agent.build_runtime
python -B -m test_agent.verify
```

Building the runtime requires network access to obtain Terraform and the provider.
Verification itself has no network access. It runs the checked-in proposal and
does not use any model API. The `Terraform mocked tests` PR workflow runs these
same commands and uploads `verification.json`, including failure results.

For live analysis, set `GEMINI_API_KEY` securely in the process environment:

```sh
python -B -m test_agent log-analytics-workspace
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
skill metadata, and python-hcl2 parses HCL. Resolved dependencies are pinned;
Python package hashes and automated dependency updates are not configured yet.

## Execution and trust boundaries

`context.py` snapshots only top-level `.tf` files and direct `tests/*.tftest.hcl`
files, with size/count limits and rejection of links and paths outside the module.
`hcl_policy.py` accepts a narrow source and test structure: the known workspace
resource, approved AzureRM provider, explicit `command = plan`, mocked provider,
literal inputs and assertions. External sources, data blocks, real provider
configuration, backend/cloud blocks, provisioners and test overrides are rejected.
Only the computed resource ID may be mocked; retention, tags and SKU stay real
Terraform expressions. This is a structural allowlist, not proof of test quality.

`runtime/Dockerfile` pins Terraform 1.17.1 by digest and AzureRM 4.81.0 by version
and committed lock hashes inherited from the base repository. Build-time init
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
before adding the proposal to the module. The author loaded the testing skill,
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
