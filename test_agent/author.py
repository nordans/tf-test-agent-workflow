"""Bounded authoring tools: propose one test, verify, optionally repair once."""

import difflib
import json

import yaml

from test_agent.context import AgentError, AnalysisContext, read_text, sha256_text
from test_agent.hcl_policy import MODULE, TEST_PATH, validate_proposal, validate_sources
from test_agent.planner import declaration, run_planner

AUTHOR_SKILL = "terraform-unit-testing"
MAX_AUTHOR_ROUNDS = 6


def run_author(context: AnalysisContext, client, runner) -> tuple[dict, str]:
    if context.module != MODULE:
        raise AgentError(
            "Test authoring currently supports log-analytics-workspace only."
        )
    validate_sources(context.files)
    target = f"modules/{MODULE}/{TEST_PATH}"
    if target in context.files:
        raise AgentError(
            "This milestone creates a new test file; updating an existing file is not enabled."
        )
    analysis = run_planner(context, client)
    if analysis["plan"]["decision"] == "no_change":
        raise AgentError("The analysis did not propose additional tests.")
    instructions = read_text(
        context.trusted_root / "agent" / "test-writer.md", context.trusted_root
    )
    skill = read_text(
        context.trusted_root / "skills" / AUTHOR_SKILL / "SKILL.md",
        context.trusted_root,
    )
    try:
        metadata = yaml.safe_load(skill.split("---", 2)[1])
        if metadata["name"] != AUTHOR_SKILL or not isinstance(
            metadata["description"], str
        ):
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError, yaml.YAMLError):
        raise AgentError("Invalid trusted authoring skill metadata.") from None
    definitions = {
        "load_skill": declaration(
            "load_skill",
            metadata["description"],
            {"name": {"type": "string", "enum": [AUTHOR_SKILL]}},
        ),
        "propose_test_file": declaration(
            "propose_test_file",
            "Propose the complete HCL content of the single allowed test file.",
            {"content": {"type": "string", "maxLength": 32768}},
        ),
        "verify_module": declaration(
            "verify_module",
            "Run the accepted proposal and its mutation check in the sandbox.",
            {},
        ),
        "submit_result": declaration(
            "submit_result",
            "Finish; the controller reports actual verification status.",
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
                            "analysis": analysis["plan"],
                            "source_data": context.module_data(),
                        }
                    )
                }
            ],
        }
    ]
    loaded, proposal, verification = False, None, None
    accepted_conditions = set()
    attempts, calls_log, hcl = [], [], ""
    for _ in range(MAX_AUTHOR_ROUNDS):
        if not loaded:
            allowed = ["load_skill"]
        elif proposal is not None and verification is None:
            allowed = ["verify_module"]
        elif verification is not None and (
            verification["passed"] or verification["terraform"]["assertion_failure"]
        ):
            allowed = ["submit_result"]
        elif len(attempts) < 2:
            allowed = ["propose_test_file"] + (["submit_result"] if attempts else [])
        else:
            allowed = ["submit_result"]
        response = client.generate(
            instructions, contents, [definitions[name] for name in allowed]
        )
        parts = response.get("parts", [])
        if any(not isinstance(part, dict) for part in parts):
            raise AgentError("Author returned malformed response parts.")
        calls = [part["functionCall"] for part in parts if "functionCall" in part]
        if (
            len(calls) != 1
            or not isinstance(calls[0], dict)
            or calls[0].get("name") not in allowed
        ):
            raise AgentError("Author must call exactly one allowed tool at a time.")
        call = calls[0]
        name, args = call["name"], call.get("args")
        contents.append(response)
        if name == "load_skill":
            if args != {"name": AUTHOR_SKILL}:
                raise AgentError(
                    "Only the registered test-authoring skill may be loaded."
                )
            loaded, result = True, {"instructions": skill}
        elif name == "propose_test_file":
            if (
                not isinstance(args, dict)
                or set(args) != {"content"}
                or not isinstance(args["content"], str)
            ):
                raise AgentError("The proposal tool accepts only HCL content.")
            hcl, proposal, verification = args["content"], None, None
            attempt = {"number": len(attempts) + 1, "proposal_sha256": sha256_text(hcl)}
            attempts.append(attempt)
            try:
                proposal = validate_proposal(hcl)
                if not accepted_conditions <= set(proposal.conditions):
                    raise AgentError(
                        "HCL policy rejected proposal: ASSERTION_REMOVAL_OR_CHANGE_REQUIRES_REVIEW."
                    )
                accepted_conditions = set(proposal.conditions)
                result = {
                    "accepted": True,
                    "path": TEST_PATH,
                    "runs": list(proposal.runs),
                }
            except AgentError as error:
                proposal = None
                result = {"accepted": False, "error": str(error)}
            attempt["policy"] = result
        elif name == "verify_module":
            if args != {}:
                raise AgentError(
                    "The verification tool does not accept commands or options."
                )
            verification, hcl = runner.verify(context.files, proposal)
            attempts[-1]["verification"] = verification
            result = verification
        else:
            if args != {}:
                raise AgentError(
                    "Result submission does not accept model-authored execution claims."
                )
            passed = verification is not None and verification["passed"]
            report = {
                "mode": "test_proposal",
                "module": MODULE,
                "passed": passed,
                "requires_review": verification is not None
                and verification["terraform"]["assertion_failure"],
                "tests_executed": any(
                    any(
                        run["status"] in ("pass", "fail", "error")
                        for run in attempt.get("verification", {})
                        .get("terraform", {})
                        .get("runs", [])
                    )
                    for attempt in attempts
                ),
                "analysis": analysis,
                "tool_calls": calls_log + [name],
                "attempts": attempts,
                "author_policy": {
                    "instructions_sha256": sha256_text(instructions),
                    "skill": AUTHOR_SKILL,
                    "skill_sha256": sha256_text(skill),
                },
                "usage": dict(client.usage),
                "proposal_path": target,
                "proposal_sha256": sha256_text(hcl),
                "diff": "".join(
                    difflib.unified_diff(
                        [],
                        hcl.splitlines(keepends=True),
                        fromfile="/dev/null",
                        tofile=target,
                    )
                ),
            }
            return report, hcl
        calls_log.append(name)
        result_part = {"name": name, "response": result}
        if "id" in call:
            result_part["id"] = call["id"]
        contents.append({"role": "user", "parts": [{"functionResponse": result_part}]})
    raise AgentError(
        "Author reached its six-request limit without submitting a result."
    )
