"""Model-only authoring stage; verification happens on a different worker."""

import json

from test_agent.author import AUTHOR_SKILL
from test_agent.context import MAX_FILE_BYTES, AgentError, read_text, sha256_text
from test_agent.hcl_policy import validate_update
from test_agent.planner import declaration, run_planner
from test_agent.pr_context import TARGET
from test_agent.pr_results import Analysis, Draft, digest


def draft_tests(bundle, trusted_root, client):
    result = Draft(
        identity=bundle.identity, bundle_sha256=digest(bundle), status="skipped"
    )
    if not bundle.eligible:
        result.reason = "; ".join(bundle.reasons) or "No supported Terraform changes."
        return result
    context = bundle.analysis_context(trusted_root)
    result.analysis = Analysis.model_validate(run_planner(context, client))
    plan = result.analysis.plan
    if any(case.basis == "proposed_requirement" for case in plan.test_cases):
        result.status, result.reason = (
            "needs_review",
            "The plan contains a proposed infrastructure requirement.",
        )
        result.usage = dict(client.usage)
        return result
    if plan.decision == "no_change":
        result.status, result.usage = "no_change", dict(client.usage)
        return result
    instructions = read_text(trusted_root / "agent" / "pr-writer.md", trusted_root)
    skill = read_text(trusted_root / "skills" / AUTHOR_SKILL / "SKILL.md", trusted_root)
    result.policy = {
        "instructions_sha256": sha256_text(instructions),
        "skill_sha256": sha256_text(skill),
    }
    definitions = {
        "load_skill": declaration(
            "load_skill",
            "Load the registered Terraform testing skill.",
            {"name": {"type": "string", "enum": [AUTHOR_SKILL]}},
        ),
        "propose_test_file": declaration(
            "propose_test_file",
            "Propose complete HCL for the fixed test file.",
            {"content": {"type": "string", "maxLength": MAX_FILE_BYTES}},
        ),
        "submit_proposal": declaration(
            "submit_proposal",
            "Hand the proposal to the separate verifier without execution claims.",
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
                        }
                    )
                }
            ],
        }
    ]
    attempts, loaded = 0, False
    for _ in range(4):
        allowed = (
            ["load_skill"]
            if not loaded
            else (
                ["submit_proposal"]
                if result.hcl is not None or attempts == 2
                else ["propose_test_file"]
            )
        )
        response = client.generate(
            instructions, contents, [definitions[name] for name in allowed]
        )
        parts = response.get("parts", [])
        if not isinstance(parts, list) or any(
            not isinstance(part, dict) for part in parts
        ):
            raise AgentError("Malformed author response.")
        calls = [part["functionCall"] for part in parts if "functionCall" in part]
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
        elif name == "propose_test_file":
            if (
                not isinstance(args, dict)
                or set(args) != {"content"}
                or not isinstance(args["content"], str)
            ):
                raise AgentError("Only HCL content is accepted.")
            attempts += 1
            try:
                if len(args["content"].encode("utf-8")) > MAX_FILE_BYTES:
                    raise AgentError("Proposal exceeds the file size limit.")
                validate_update(
                    bundle.before_files.get(TARGET),
                    args["content"],
                    bundle.before_files,
                    bundle.after_files,
                )
                result.hcl = args["content"]
                tool_result = {"accepted": True, "path": TARGET}
            except AgentError as error:
                tool_result = {"accepted": False, "error": str(error)}
                result.reason = str(error)
        else:
            if args != {}:
                raise AgentError(
                    "Submission accepts no paths, commands or execution claims."
                )
            result.status = "proposed" if result.hcl is not None else "needs_review"
            if result.hcl is not None:
                result.reason = ""
            result.usage = dict(client.usage)
            return result
        response_part = {"name": name, "response": tool_result}
        if "id" in call:
            response_part["id"] = call["id"]
        contents.append(
            {"role": "user", "parts": [{"functionResponse": response_part}]}
        )
    raise AgentError("PR author exceeded its four-request budget.")
