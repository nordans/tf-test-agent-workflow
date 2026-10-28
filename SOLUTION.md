# Solution

## Current milestone

The repository currently contains a read-only module test planner and a manual
Gemini connectivity check. Test generation, Terraform execution, PR diff analysis,
and publishing agent results to a PR are not implemented yet. The final example
PR demonstrating those capabilities will be added after the workflow exists.

## Decisions and execution

The agent uses Python and Gemini function calling through a small REST adapter.
This makes the tool loop explicit and inspectable, at the cost of maintaining
protocol handling ourselves. Pydantic validates tool submissions; PyYAML reads
skill metadata. Resolved dependency versions are pinned in `requirements.txt`.
Dependency hashes and an automated update process are not configured yet.

From an activated Python 3.12 virtual environment at the repository root:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m test_agent log-analytics-workspace
```

Set `GEMINI_API_KEY` securely in the process environment before running the live
planner. Do not put a literal key in shell history, a committed file, or a report.
The live command sends module source to Gemini and consumes that project's quota.
No Azure account or credentials are used. The PR checks run offline with mock
model responses and require no Gemini secret. The connectivity workflow is
separate and runs only on manual dispatch on `main`.

## Agent, skill, and tools

- `agent/instructions.md` defines the custom planner's role and trust boundaries.
- `skills/terraform-change-analysis/SKILL.md` is the reusable analysis procedure.
  Its description is advertised first; its full instructions reach the model only
  when it invokes `load_skill`.
- `test_agent/context.py` snapshots top-level `.tf` files and direct
  `tests/*.tftest.hcl` files from one caller-selected module. It rejects paths,
  linked module/test directories, linked source files, and oversized inputs.
- `test_agent/planner.py` dispatches `load_skill`, `read_module`, and
  `submit_test_plan`. Each discovery tool is usable once. Submission is available
  only after both discovery tools have run. There are at most three model
  requests, each with at most 4096 output tokens; there are no automatic retries.
- `test_agent/models.py` defines the plan contract, including implemented behavior
  versus proposed requirements. Unknown fields and invalid evidence locations
  are rejected. These checks do not prove the semantic truth of a model's claims.

The model cannot select arbitrary file paths, execute commands, change source or
tests, access environment variables, or perform GitHub writes. Source comments
are untrusted data. Policy text comes from `trusted_root`, separately from
`source_root`. The local CLI deliberately uses the current checkout for both:
run it only from a checkout whose agent code and policies you trust. The future
PR workflow must use trusted base code/policies and a separate PR source snapshot.

## Example and verification

`docs/examples/log-analytics-workspace.plan.json` contains an actual Gemini run,
not a hand-written model response. It records invoked tools, token usage, policy
and source SHA-256 hashes, the decision, evidence, and limitations. Hashes identify
the analyzed content; they are not a signature or proof of correctness.

The module has no existing native test files. Review the recorded cases against
its source; the planner's output is a proposal requiring human assessment.
The recorded run called `load_skill`, `read_module`, then `submit_test_plan`, chose
`add_tests`, proposed three cases, and used 2859 tokens in total. Human review
confirmed the default retention of 30, the fixed SKU, and the output reference.
The override case is still general: concrete custom retention/tag values must be
chosen before authoring its test. Valid output structure is not enough to ensure
every case is sufficiently specific.
`tests_executed` is always set to `false` by Python. No Terraform tests were created
or executed in this milestone. Offline tests exercise tool gating, source
boundaries, result validation, signature preservation, safe API failures, and
credential handling. The symlink check may be skipped on Windows without symlink
privileges; it runs on the Linux PR runner.

## Limitations and next steps

This stage analyzes a complete module, not a PR diff, and reads only the supported
file set above; it does not follow nested modules or read helper scripts. It does
not measure coverage, query Azure schemas, or verify deployment behavior. A file
and line reference proves the location exists, not that the proposed assertion is
correct. The small model may omit relevant cases or make unsupported claims.

Next stages will add test-authoring and findings skills, controlled test proposals,
isolated mocked Terraform execution, and GitHub reporting tied to an exact PR
revision. Decisions about infrastructure changes remain with people. More modules
and teams would need change/impact selection, independent module budgets, provider
caching, and maintained policy ownership. Production use would also need stronger
sandboxing for untrusted execution, secret and data-handling policies, abuse/cost
controls for public PRs, dependency verification, model evaluations, and operational
monitoring. The current planner is not a complete production PR workflow.
