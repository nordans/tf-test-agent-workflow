# Solution

## Scope and decisions

The primary interface is a GitHub PR workflow implemented in Python. It discovers
changed leaf modules under modules/ from Git objects, including modules added in
the PR, and processes each independently. Module names and AzureRM resource types
are not enumerated in policy. The approved provider schemas determine resource
availability and which computed values may be mocked.

Python owns paths, tool availability, validation, execution and publication.
Gemini chooses test cases and proposes HCL through bounded tools. This keeps
execution decisions inspectable. The tradeoff is a deliberately constrained HCL
subset and explicit human review when the subset cannot handle a change.

The output is a verified test patch for the originating PR. An operator reviews
and applies it there. Infrastructure decisions remain with people. No Git write,
merge or additional-PR tool is exposed to the agent.

The source starts from cf-recruitment/tf-platform-base. Current provider support
is AzureRM 5.11.0 and the locally rebuilt external 2.4.2+patched.1. New provider
dependencies require a reviewed runtime update.

## Prerequisites and running

Use Python 3.12, uv 0.13.6, Git, an amd64 Linux Docker engine, Betterleaks 1.9.0,
a trusted checkout and a Gemini API key. Model calls send selected module source
to Gemini and consume API quota. Runtime builds and security databases require
network access; Terraform verification has none.

~~~sh
uv sync --locked
uv run --locked python -B -m test_agent.build_runtime
uv run --locked python -B -m test_agent.pr_workflow local \
  --repo /path/to/checkout --base BASE_SHA --head HEAD_SHA \
  --output .agent-output/pr-run
~~~

Set GEMINI_API_KEY securely in the process environment, never as a command-line
argument or committed value. Supply full Git commit IDs and a new output directory.
Use --secret-scanner if Betterleaks is outside PATH. Local execution scans source
before model calls and writes context.json, draft.json, report.json, proposal.patch
and comment.md. Its results are marked local and cannot be published by the PR
publisher.

On GitHub, configure the GEMINI_API_KEY secret and merge the workflows to main.
PR test agent runs on same-repository PRs to main; manual workflow_dispatch takes
a PR number. It skips model calls for fork, bot and draft PRs. Changed controller,
skill, runtime or workflow code requires review rather than running itself.

## Agent, skills and execution

The custom agent's analysis instructions are in agent/instructions.md and its
authoring instructions in agent/pr-writer.md. Registered reusable skills are:

- skills/terraform-change-analysis/SKILL.md: inspect before/after sources,
  inventory behavior and existing coverage, propose evidence-backed cases and
  findings, separate implemented behavior from desired requirements.
- skills/terraform-unit-testing/SKILL.md: produce complete native test files,
  use computed mocks, preserve accepted checks and hand proposals to verification.

planner.py exposes load_skill, read_module and submit_test_plan. Discovery tools
are usable once; submission is available after discovery. Evidence must identify
a supplied file and real line. One structural contract correction is allowed,
within four requests; invalid evidence and unknown tools are rejected.

pr_author.py runs the planner per module, then exposes load_skill,
propose_test_files and submit_proposal. It allows two structural proposals within
four authoring requests. Paths are an enum of that module's existing tests plus
tests/generated.tftest.hcl. Omitted files stay unchanged; deletion is unsupported.
A plan proposing infrastructure requirements requires human review.
Ordinary Terraform assertion failures do not trigger model repair.

The earlier standalone planner/bootstrap remains for compatibility. Its
workspace-only author and HCL policy are not called by the PR workflow; use
pr_workflow local for general authoring.

## Four GitHub workers

.github/workflows/pr-test-agent.yml uses pull_request_target, checking out the
trusted workflow commit from main in every worker, never the PR branch.

1. Prepare reads PR identity and Git snapshots and scans source before upload.
   Source is scanned whenever present, even if a module is ineligible for Gemini.
2. Propose builds the approved runtime for schema checks and calls the agent.
   Only the model step receives GEMINI_API_KEY. It has no GitHub write permission.
   Schema queries execute trusted provider binaries without PR source mounted.
3. Verify independently rereads Git revisions, checks artifact/source/schema
   digests, validates candidate HCL and executes complete suites and mutations.
   It receives no model or repository-write credential.
4. Publish checks identities and stage outcomes, rechecks PR base/head/open state,
   and updates only its marked github-actions bot comment. Only this worker has
   pull-requests: write. It executes neither Terraform nor model-generated code.

Context, draft and verification artifacts bind the repository, PR, base/head and
merge-base revisions. SHA-256 digests detect mismatched payloads; they are not
cryptographic attestations against a compromised worker. Publishing has a residual
API race after its final identity check; the comment always names analyzed SHAs.
Concurrency cancels superseded runs. Artifacts expire after seven days.

## HCL and execution controls

terraform_policy.py admits top-level Terraform sources, resources, data, variables,
locals and outputs for approved providers. Provisioners, connections, provider
overrides, backend/cloud configuration, nested/remote modules and filesystem,
environment or execution functions are excluded. Every provider must be mocked
and every test run must explicitly plan. Inputs and mock defaults are literals.

Mock fields are checked against runtime provider schemas. Configurable attributes
cannot be mocked. Legacy SDK schemas mark computed IDs Optional; ID mocking is
allowed only when computed and never when an ID is explicitly configured.
Nested mock blocks may contain computed fields, such as identity.principal_id.
Output/computed assertions require explicit plan-time mocks. New module outputs,
newly added outputs and planned output coverage must have assertions; structural
repair cannot silently remove that coverage. This checks references, not the
semantic strength of every assertion.

The complete existing test suite is preserved. Old runs, inputs, expected
validation failures and mock values remain; computed defaults may be added.
Inputs for newly introduced variables may be added. A changed literal scalar
source expectation may update its matching equality when the run does not
override that variable. Boolean expectation changes require review. This limits
automatic rewrites while allowing new modules and ordinary default changes.

suite_runner.py runs all candidate test files together. Success requires the exact
qualified file/run pairs, completed passing runs, a nonempty matching summary,
no skips/errors and a successful process exit. Formatting-only proposals become
no_change_verified after execution.

The verifier tries at most three temporary source mutations, preferring referenced
arguments and exercised input variations. It freezes variable wiring to a known
literal input or flips a configured Boolean. An actual test assertion failure
counts as detection; a provider crash, init error or invalid configuration does
not. No detected mutation produces needs_review, including for a no-change plan.
One detected mutation demonstrates sensitivity, not comprehensive coverage.

Containers run as UID 65532, without network, with read-only root/input, all
capabilities dropped, no-new-privileges and CPU/RAM/PID limits. Writable storage
is ephemeral noexec tmpfs. Formatting operates on writable copies in that tmpfs;
the input mount remains read-only. Terraform CI exercises both runner entry points
with unformatted inputs. Only bounded Terraform/test snapshots are mounted;
scripts, home directories and the Docker socket are not. The Docker CLI receives
an environment allowlist excluding Gemini, GitHub and Azure secrets. Host kernel
sharing remains a limitation of this boundary.

Budgets: eight modules/PR, 128 changed paths, 32 files/module, 16 KiB/file,
96 KiB/module and 256 KiB/revision. Model stages allow 64 calls total and stop
further requests after 250,000 reported tokens or 15 minutes. One response can
cross the token threshold. Jobs have fixed timeouts. Each Terraform container has
a 120-second/1-MiB output limit. Trusted provider-schema output has a separate
64-MiB bound. Git calls are bounded and disable hooks, external diffs/textconv,
replacement objects and credential inheritance.

## Verification and examples

Offline checks run Ruff, pytest, Actionlint/ShellCheck, Betterleaks and pip-audit.
Pinned actions and checksum-verified tools are used. Terraform CI runs all existing
module suites, requires tests and a detected mutation for changed modules, and
reports missing tests or undetected mutations in unchanged modules. All eight
modules have checked-in suites and currently detect a sampled source mutation.
Passing CI does not mean full repository coverage.

check_modules.py separately validates all eight modules and the stack against
offline provider schemas and runs a fixed mocked external smoke test. The runtime
pins Terraform 1.17.1 by image digest and authenticates provider packages with
lockfile hashes. Seven modules and the stack require AzureRM 5.x; the workspace
retains its already tested >= 4.0, < 6.0 constraint. Subnet service endpoint blocks
were migrated with populated/empty-list assertions. Mocked plans and validation
do not establish live Azure upgrade safety.

The official external 2.4.2 package introduced HIGH/CRITICAL Go advisories. The
runtime rebuilds pinned upstream source with pinned Go and frozen patched
dependencies, checks the rebuilt package hash and runs provider tests. See
[runtime/external-build/README.md](runtime/external-build/README.md). This adds a
maintenance obligation until a suitable official package is available.
Trivy scans the final image and blocks HIGH/CRITICAL, including unfixed findings;
there are no suppressions. Four previously recorded UNKNOWN findings need
manual triage. Scanner/database failures also fail checks.

The main ruleset requires Code quality CI, Workflow validation, Secret scan,
Dependency audit and Terraform CI, from GitHub Actions. PRs are required; force
pushes/deletion and bypass actors are disabled. Strict up-to-date branch checking
is disabled. GitHub settings are not copied when cloning this repository.

[PR #10](https://github.com/nordans/tf-test-agent-workflow/pull/10) demonstrated the
earlier single-module integration: the agent detected a stale retention test,
published a verified patch and later updated the same comment with
no_change_verified after application.

The multi-module local example uses a temporary Git history adding an unseen
Azure AI Search module and changing workspace retention from 30 to 60. Real Gemini
loads both skills; the independent runner verifies the new suite and updated
existing suite, then reruns after patch application. The recorded example is in
[docs/examples/generic-pr-run.json](docs/examples/generic-pr-run.json). It is local
evidence, not a claim that the new workflow has already run from trusted main.
Synthetic sources are under tests/fixtures. The actual four-module publication
path was subsequently verified in [PR #16](https://github.com/nordans/tf-test-agent-workflow/pull/16):
all modules returned no_change_verified with mutation detection and an empty patch.

The existing data-factory, databricks-workspace, function-app and private-endpoint
modules were bootstrapped together with real Gemini calls, followed by explicit
reviewer edits and independent verification. Their 11 new scenarios all passed
and each suite detected a source mutation. The complete repository then passed
22 native test runs across eight modules; network's sampled mutation gap remained
at that checkpoint.
See [the bootstrap record](docs/examples/four-module-bootstrap.json) for decisions,
skill/tool usage, failed attempts, review feedback, edits and execution results.

The first unattended batch did not pass: there were omitted outputs, insufficient
mutation sensitivity, forbidden configurable mock fields and HTTP 429 responses.
A later attempt exposed readonly copied files preventing formatting; the runner
fix is isolated in [PR #15](https://github.com/nordans/tf-test-agent-workflow/pull/15).
Local follow-ups used a paced transport wrapper and human feedback. Review also
restored assertions removed during a model repair, split them into bounded runs,
and added missing resource wiring checks. These are assisted, reviewed results,
not evidence that the agent autonomously produced complete coverage on one try.

Network's original three scenarios used fixed subnet inputs and checked counts,
names, endpoint presence and CIDR validation. Freezing NSG/association iteration to
that map still passed. Four added scenarios exercise caller overrides, different
keys, a third subnet, the empty map, resource references and outputs. Network now
passes seven scenarios and detects a valid variable-wiring mutation; all eight
modules pass 26 scenarios and detect a sampled mutation. Six additional controlled
regressions passed the old suite and are rejected by the expanded suite. Some
produce configuration errors instead of assertion failures; those are explicitly
not counted as strict mutation detection. See
[the network regression record](docs/examples/network-coverage.json).

Computed mock IDs in these network tests are shared per resource type. They verify
nominal output/reference contracts and key sets, but do not distinguish every
possible cross-instance ID swap. Alternate caller keys catch hardcoded key names.
The generic variable-freezing mutator also does not reconstruct defaults supplied
by Terraform type conversion, such as optional service_endpoints. A control that
loses such a default can become invalid and is never credited as detected.

Existing configuration still warrants owner review: data-factory,
databricks-workspace and function-app default public network access to true;
function-app configures AllAllowed for FTPS and Storage
Blob Data Owner for its runtime storage role. The agent noted missing tests and
external/identity dependencies; these additional configuration observations came
from reviewer inspection. Tests document implemented behavior and do not approve
the security posture or change infrastructure settings.

## Limits, assumptions and further work

Stack/root impact warnings keep the overall result at needs_review while allowing
verified patches for independent changed leaf modules. They do not broaden native
execution to stack code.

The flat modules/ layout and approved provider schemas define execution scope.
Stack impact analysis, removed modules, external program changes, aliases, nested
module dependencies and other providers require review. One unsupported module
does not hide supported module results, but protected policy changes stop model
execution for the PR. Whole-job timeouts can prevent completion of remaining
modules; publication then reports missing verification rather than a pass.

Source, diff and model text remain untrusted. Location evidence can be correct
while reasoning is wrong. Literal-equality updates and mutation heuristics cover
a useful subset, not arbitrary refactors. Tests cannot prove Azure acceptance,
effective permissions, service behavior, networking, deployment success or script
logic. Mocked IDs establish wiring only. Generated tests need human review.

Next work is to confirm the final GitHub infrastructure-change example and review
remaining coverage gaps, including type-preserving source mutations. Model repair
needs stronger plan-to-assertion checks and more precise structural diagnostics;
production transport still needs bounded transient-error handling. With more
modules/teams/repositories, use dependency impact analysis,
parallel isolated workers, per-module runtimes and policy ownership, persistent
progress, stronger artifact attestation and a stronger execution boundary.
Production extensions include cost/abuse controls, model evaluations for useful
assertions and security findings, operational metrics and consolidation of the
legacy bootstrap interface. Keep provider/runtime updates reviewed and scanned.
