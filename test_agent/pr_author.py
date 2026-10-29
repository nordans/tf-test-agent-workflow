"""Bounded multi-module planning and authoring, without execution or write tools."""

import json
import time

from test_agent.behavior import required_booleans, validate_boolean_coverage
from test_agent.context import (
    AUTHOR_SKILL,
    MAX_FILE_BYTES,
    AgentError,
    read_text,
    sha256_text,
)
from test_agent.planner import declaration, run_planner
from test_agent.pr_results import (
    Analysis,
    Draft,
    ModuleDraft,
    digest,
    same_test_content,
)
from test_agent.terraform_policy import (
    provider_schema,
    required_outputs,
    test_files,
    validate_output_coverage,
    validate_sources,
    validate_update,
)


class BudgetedClient:
    def __init__(self, client):
        self.client, self.calls, self.deadline = client, 0, time.monotonic() + 900

    @property
    def usage(self):
        return self.client.usage

    def generate(self, *args):
        if (
            self.calls >= 64
            or self.usage.get("totalTokenCount", 0) >= 250000
            or time.monotonic() >= self.deadline
        ):
            raise AgentError(
                "The PR model request, token or time budget was exhausted."
            )
        self.calls += 1
        return self.client.generate(*args)


def computed_hints(schema):
    fields = {
        name: attr.get("type", attr.get("nested_type", {}))
        for name, attr in schema.get("attributes", {}).items()
        if attr.get("computed")
        and not attr.get("required")
        and (not attr.get("optional") or name == "id")
    }
    for name, body in schema.get("block_types", {}).items():
        nested = computed_hints(body["block"])
        if nested:
            fields[name] = {"nesting": body["nesting_mode"], "computed_fields": nested}
    return fields


def schema_hints(files, schemas):
    source = validate_sources(files)
    return {
        key: {
            kind: computed_hints(
                provider_schema(schemas, kind, data=key == "mock_data")
            )
            for kind, _ in instances
        }
        for key, instances in (
            ("mock_resource", source.resources),
            ("mock_data", source.data_sources),
        )
    }


def draft_one(bundle, module, trusted_root, client, schemas):
    result = ModuleDraft(module=module, status="skipped")
    before, after = (
        bundle.module_files(module, "before"),
        bundle.module_files(module, "after"),
    )
    old_tests, current = test_files(before, module), test_files(after, module)
    hints = schema_hints(after, schemas)
    context = bundle.analysis_context(trusted_root, module)
    usage_before = dict(client.usage)
    result.analysis = Analysis.model_validate(run_planner(context, client))
    result.analysis.usage = {
        k: v - usage_before.get(k, 0) for k, v in client.usage.items()
    }
    result.usage = dict(result.analysis.usage)
    plan = result.analysis.plan
    if any(case.basis == "proposed_requirement" for case in plan.test_cases):
        result.status, result.reason = (
            "needs_review",
            "The plan proposes an infrastructure requirement; human review is required.",
        )
        return result
    if plan.decision == "no_change":
        result.status = "no_change"
        result.usage = dict(result.analysis.usage)
        return result
    instructions = read_text(trusted_root / "agent/pr-writer.md", trusted_root)
    skill = read_text(trusted_root / "skills" / AUTHOR_SKILL / "SKILL.md", trusted_root)
    result.policy = {
        "instructions_sha256": sha256_text(instructions),
        "skill_sha256": sha256_text(skill),
    }
    allowed_paths = sorted(set(current) | {"tests/generated.tftest.hcl"})
    definitions = {
        "load_skill": declaration(
            "load_skill",
            "Load the Terraform unit-testing skill.",
            {"name": {"type": "string", "enum": [AUTHOR_SKILL]}},
        ),
        "propose_test_files": declaration(
            "propose_test_files",
            "Propose complete HCL for allowed module test files; omitted files stay unchanged.",
            {
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "enum": allowed_paths},
                            "content": {"type": "string", "maxLength": MAX_FILE_BYTES},
                        },
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                }
            },
        ),
        "submit_proposal": declaration(
            "submit_proposal",
            "Submit for independent verification; do not claim execution results.",
            {},
        ),
    }
    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "text": json.dumps(
                        {
                            "analysis": plan.model_dump(),
                            "source_data": context.module_data(),
                            "allowed_test_paths": allowed_paths,
                            "computed_mock_fields": hints,
                            "limits": {
                                "test_files": 8,
                                "runs_per_file": 12,
                                "assertions_per_run": 12,
                                "bytes_per_test_file": MAX_FILE_BYTES,
                            },
                            "required_output_assertions": sorted(
                                required_outputs(before, after, plan)
                            ),
                            "required_boolean_assertions": required_booleans(
                                before, after
                            ),
                        }
                    )
                }
            ],
        }
    ]
    loaded, attempts, accepted = False, 0, None
    for _ in range(4):
        allowed = (
            ["load_skill"]
            if not loaded
            else ["submit_proposal"]
            if accepted is not None or attempts == 2
            else ["propose_test_files"]
        )
        response = client.generate(
            instructions, contents, [definitions[n] for n in allowed]
        )
        parts = response.get("parts", [])
        if not isinstance(parts, list) or any(not isinstance(p, dict) for p in parts):
            raise AgentError("Malformed author response.")
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if (
            len(calls) != 1
            or not isinstance(calls[0], dict)
            or calls[0].get("name") not in allowed
        ):
            raise AgentError("Author must request exactly one available tool.")
        call = calls[0]
        name, args = call["name"], call.get("args")
        contents.append(response)
        result.tool_calls.append(name)
        if name == "load_skill":
            if args != {"name": AUTHOR_SKILL}:
                raise AgentError("Only the registered testing skill is available.")
            loaded, tool_result = True, {"instructions": skill}
        elif name == "propose_test_files":
            attempts += 1
            try:
                if (
                    not isinstance(args, dict)
                    or set(args) != {"files"}
                    or not isinstance(args["files"], list)
                    or not 1 <= len(args["files"]) <= 8
                ):
                    raise AgentError(
                        "Only a bounded list of test file contents is accepted."
                    )
                edits = {}
                for file in args["files"]:
                    if (
                        not isinstance(file, dict)
                        or set(file) != {"path", "content"}
                        or file["path"] not in allowed_paths
                        or file["path"] in edits
                        or not isinstance(file["content"], str)
                        or len(file["content"].encode("utf-8")) > MAX_FILE_BYTES
                    ):
                        raise AgentError(
                            "Proposal contains an invalid path, duplicate or oversized file."
                        )
                    edits[file["path"]] = file["content"]
                candidate = {**current, **edits}
                suite = validate_update(before, after, old_tests, candidate, schemas)
                validate_update(
                    before, after, current, candidate, schemas, previous_sources=after
                )
                validate_output_coverage(before, after, suite, plan)
                validate_boolean_coverage(before, after, suite)
                accepted = candidate
                result.edits = {
                    p: text
                    for p, text in edits.items()
                    if not same_test_content(current.get(p), text)
                }
                tool_result = {"accepted": True, "changed_files": sorted(result.edits)}
                result.reason = ""
            except AgentError as error:
                result.reason = str(error)
                tool_result = {"accepted": False, "error": str(error)}
        else:
            if args != {}:
                raise AgentError(
                    "Submission accepts no paths, commands or execution claims."
                )
            result.status = (
                "needs_review"
                if accepted is None
                else "proposed"
                if result.edits
                else "no_change"
            )
            result.usage = {
                k: v - usage_before.get(k, 0) for k, v in client.usage.items()
            }
            return result
        response_part = {"name": name, "response": tool_result}
        if "id" in call:
            response_part["id"] = call["id"]
        contents.append(
            {"role": "user", "parts": [{"functionResponse": response_part}]}
        )
    raise AgentError("Module author exceeded its four-request budget.")


def draft_tests(bundle, trusted_root, client, schemas=None, schema_sha256=None):
    result = Draft(
        identity=bundle.identity,
        bundle_sha256=digest(bundle),
        schema_sha256=schema_sha256,
    )
    guarded = BudgetedClient(client) if client is not None else None
    for module in bundle.selected_modules:
        reason = "; ".join(bundle.blocking_reasons) or bundle.module_reasons.get(module)
        if reason or not bundle.eligible:
            result.modules.append(
                ModuleDraft(
                    module=module,
                    status="needs_review",
                    reason=reason or "No supported module source.",
                )
            )
            continue
        try:
            if guarded is None or schemas is None:
                raise AgentError("Approved schemas and a model client are required.")
            result.modules.append(
                draft_one(bundle, module, trusted_root, guarded, schemas)
            )
        except AgentError as error:
            result.modules.append(
                ModuleDraft(module=module, status="needs_review", reason=str(error))
            )
        except (ValueError, TypeError, KeyError, IndexError):
            result.modules.append(
                ModuleDraft(
                    module=module,
                    status="needs_review",
                    reason="Invalid module analysis or author response.",
                )
            )
    if not result.modules:
        result.reason = "; ".join(bundle.reasons) or "No Terraform module changes."
    return result
