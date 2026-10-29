# Solution

## Design

The primary interface is a GitHub PR workflow with a custom Python agent. Python
selects changed leaf modules from immutable Git objects, assigns tools and paths,
validates proposals, runs Terraform and publishes results. Gemini chooses test
cases and proposes HCL; it cannot change its permissions or execution evidence.
Explicit loops and Pydantic contracts keep this inspectable without a large agent
framework. The tradeoff is a constrained HCL subset and human review of unsupported
changes rather than general shell access.

The output is a verified patch for the originating PR. An operator reviews and
applies it there. There is no automatic commit, merge, extra-PR or infrastructure
edit tool. Native plan tests use mocked providers and require no Azure credentials;
live authoring needs a Gemini API key and sends selected source to that vendor.

## Running

Requirements: Python 3.12, uv 0.13.6, Git, Linux amd64 Docker, Betterleaks 1.9.0 and
GEMINI_API_KEY for model calls. Run uv sync --locked, then
uv run --locked python -B -m test_agent.build_runtime.

~~~sh
uv run --locked python -B -m test_agent.pr_workflow local \
  --repo /path/to/repo --base BASE_SHA --head HEAD_SHA \
  --output .agent-output/new-run
~~~

Use full SHAs, a new output directory and --secret-scanner if Betterleaks is outside
PATH. Source is scanned before calls. Local results cannot be published as GitHub
results. Offline checks are ruff check ., ruff format --check ., python -m pytest,
python -m test_agent.verify_repository and python -m test_agent.check_modules,
run through uv run --locked after setup.

On GitHub, set the repository secret and install workflows on main. PR test agent
runs automatically or via workflow_dispatch with a PR number on main. Same-repo,
non-draft, non-bot PRs are eligible; forks do not call Gemini. See README for patch
review/application. Artifacts expire after seven days; saved example evidence is
committed separately.

## Agent and reusable skills

Analysis instructions are in agent/instructions.md and authoring instructions in
agent/pr-writer.md. planner.py exposes load_skill, read_module and submit_test_plan.
The model loads skills/terraform-change-analysis/SKILL.md, inventories behavior and
coverage, cites real before/after lines and separates implemented behavior from
proposed infrastructure requirements. Evidence validates location, not reasoning.

pr_author.py then exposes load_skill, propose_test_files and submit_proposal.
skills/terraform-unit-testing/SKILL.md covers native mocks, inputs, outputs,
preservation and structural correction. Paths are existing module tests plus
tests/generated.tftest.hcl; omitted files remain. The schema supplies computed mock
fields and limits. Skills guide decisions; Python enforces the boundaries.

The planner receives an inventory of configured literal Boolean properties.
New or changed properties require direct assertions of their source values,
including nested blocks; both author and verifier enforce this independently.
The inventory is limited to 64 properties and omits instance indices. It does
not establish expression semantics or coverage of every repeated instance.
Skills require concrete validation boundaries, caller overrides and security
expectations. Python checks concrete inputs for recognized nonempty-text and
conjunctive numeric-bound/integer validations, including invalid examples and
valid numeric boundaries. Alternatives and unknown clauses are not treated as
understood constraints; arbitrary validation completeness still needs review.

Planner and author each have four requests; one structural planner correction and
two author proposals are allowed. Runtime assertion failures end autonomous
authoring. Existing tests are preserved, including inputs and expected failures.
Source-backed scalar equality updates are supported; Boolean expectation changes
require review. Output coverage cannot be silently dropped during repair. The
older standalone workspace bootstrap remains for compatibility and is not the
general PR engine.

## GitHub workers and security

pull_request_target always checks out the trusted workflow commit from main,
never PR code. Policy/runtime/workflow changes require review before execution.

| Worker | Responsibility | Credentials |
| --- | --- | --- |
| Prepare | Read PR identity/Git snapshots and scan source before upload | GitHub read |
| Propose | Read approved schemas, plan and propose tests | GitHub read; Gemini key only in model step |
| Verify | Reread Git, bind artifacts/schemas, enforce HCL and execute suites | Contents read; no model or write key |
| Publish | Check identities/stage outcomes and update its marked bot comment | PR write only here; no Terraform execution |

Artifacts bind repository, PR, base/head/merge-base and SHA-256 digests. These detect
mismatches, not compromised-worker forgery. Publication rechecks open state and
SHAs; a small API race remains after the final check. Comments name analyzed SHAs.
Concurrency cancels superseded runs, and model text is escaped before publication.

The policy allows approved AzureRM/external leaf-module source, pure expressions,
literal test inputs, computed-only mocks and explicit plan. It rejects unmocked
providers, apply, aliases/overrides, nested modules, backend/cloud, provisioners and
filesystem/execution functions. Legacy SDK computed IDs have a narrow exception
only when not configured in source. Nested block defaults are single objects.
Unchanged module locks are excluded; changed locks and external programs require
review for that module. Execution uses runtime locks, not PR-selected packages.

Containers have no network, a readonly root/input, UID 65532, dropped capabilities,
no-new-privileges, CPU/RAM/PID limits and ephemeral noexec tmpfs. Only bounded source,
tests and approved locks are mounted; scripts, home and Docker socket are absent.
Docker receives an environment allowlist excluding model, GitHub and Azure secrets.
Formatting writes only temporary copies. Containers share the host kernel.

Budgets are eight modules, 128 changed paths, 32 files/module, 16 KiB/file,
96 KiB/module and 256 KiB/revision. Test files allow 16 runs and 12 assertions per
run; recognized validation inventories allow 32 examples. Model calls stop before further requests after
64 calls, 250,000 reported tokens or 15 minutes; one response may cross the token
threshold. A container has 120 seconds/1 MiB output; trusted schema output is capped
at 64 MiB. Git/API responses and job durations are bounded. Transient API failures
remain explicit review/rerun outcomes; automatic backoff is a production extension.

## Verification and recorded example

Success requires exact file/run pairs, complete passing runs, matching nonempty
summary, no skips/errors and exit zero. The verifier executes the before-source
baseline when present, existing head tests and the full candidate. It then tries
at most three temporary source mutations. Only an actual assertion failure counts
as detection; invalid configuration and provider/init failures do not. no_change
is independently executed before it can become no_change_verified.
Failed native assertions are included as bounded diagnostics in the PR comment.
When execution contradicts a planner's no_change decision, the comment says so.
The last path/size-bounded draft rejected by policy or verification remains in
unverified.patch, with at most two bounded rejection diagnostics in draft.json;
proposal.patch contains only independently verified module changes. Mutation
reports distinguish surviving controls from setup/runtime errors. Attribute paths
retain nested block names when prioritizing the bounded three-control sample.

[Example PR #24](https://github.com/nordans/tf-test-agent-workflow/pull/24) remains
open and unmerged. It adds a previously unseen private AKS module and changes
retention 30 to 60 with the old test left intact. [Permanent evidence](docs/examples/final-pr.json)
retains actual identities, skills/tools, rejections, native results and patch hashes.

1. [Generation](https://github.com/nordans/tf-test-agent-workflow/actions/runs/40847785251):
   the agent authored twelve AKS scenarios and a retention update. Structural
   feedback corrected an invalid nested mock. Independent execution then rejected
   AKS because resource-block list accesses lacked indices. Retention was verified:
   proposal.patch contained its update; unverified.patch contained the AKS draft.
   The bot published the actual failure in the originating PR. Initial CI also
   correctly failed for the missing/stale checked-in tests.
2. Operator review: commit de05669 applied the exact verified retention patch and
   corrected six list indices in the agent's AKS draft. Its assertions and inputs
   were retained. Commit 1d56d61 added two separate reviewer scenarios for caller
   wiring, empty tags and fixed settings. This participation is recorded explicitly;
   the result is not claimed as one-shot autonomous success.
3. [After review](https://github.com/nordans/tf-test-agent-workflow/actions/runs/40849967881):
   both modules became no_change_verified and both patch artifacts were empty.
   [Terraform CI](https://github.com/nordans/tf-test-agent-workflow/actions/runs/40849972758)
   passed 40 scenarios across nine modules and all nine sampled-mutation controls.
   A separate temporary private-API regression now fails a real assertion; the
   older six-test draft had allowed that same regression.

Historical [Foundry](docs/examples/foundry-experiment.json),
[AKS audit](docs/examples/aks-quality-audit.json),
[bootstrap](docs/examples/four-module-bootstrap.json) and
[network](docs/examples/network-coverage.json) records retain unsuccessful attempts
and reviewer contributions. Experimental PR #20 was closed without merging its
infrastructure. The final example contains no Foundry changes.

Main has eight suites and 26 scenarios; all detect a sampled mutation. Required
checks are Code quality CI, Workflow validation, Secret scan, Dependency audit and
Terraform CI. They use pinned actions/tools, Ruff/pytest, Betterleaks, pip-audit,
offline validation of all modules/stack and Trivy. HIGH/CRITICAL, including unfixed
findings, fail CI with no suppressions. The recorded image has zero HIGH/CRITICAL
and four UNKNOWN findings requiring further triage.

Terraform 1.17.1 and AzureRM 5.11.0 are pinned. external 2.4.2+patched.1 rebuilds
official pinned source with pinned Go and frozen patched dependencies, provider
tests and an authenticated local package hash; see runtime/external-build/README.md.
This is a maintenance obligation until an appropriate official package is available.

## Assumptions and limits

The repository, GitHub platform, trusted main and runtime maintainers are trusted.
Supported source uses a flat modules layout and approved provider schemas. One
unsupported module does not hide independent results; global policy changes block
model execution. Stack/root impact is review-only, with separate schema validation.

Mocked tests cannot show Azure acceptance, deployed permissions, connectivity,
application/script behavior or complete coverage. IDs are shared per mocked type
and cannot distinguish every cross-instance swap. Variable-freezing controls can
lose defaults supplied by Terraform type conversion; such invalid controls receive
no detection credit. Existing inherited deployment locks need separate maintenance;
the workflow does not validate their consistency with deployment constraints.

LLM decisions remain fallible and nondeterministic. Location evidence and structural
guards do not prove every assertion useful. Larger refactors, new providers,
dependencies and intended security changes require people. Whole-job timeouts can
leave modules unfinished; missing verification is never reported as passed.

For more teams/modules/repositories, add dependency impact analysis, separately
versioned runtimes, parallel isolated verification, policy ownership and stronger
artifact attestation. Production work would add a prebuilt scanned/signed runtime,
bounded API retry/cost controls, metrics, semantic evaluations, type-preserving
mutations and stronger execution isolation, while consolidating the legacy CLI.
