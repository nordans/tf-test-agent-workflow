"""The agent loop and the only operations a model is allowed to request."""

from pydantic import ValidationError

from test_agent.behavior import boolean_properties
from test_agent.context import SKILL_NAME, AgentError, AnalysisContext
from test_agent.gemini import MODEL
from test_agent.models import TestPlan

MAX_ROUNDS = 4


def declaration(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "parametersJsonSchema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def run_planner(context: AnalysisContext, client) -> dict:
    definitions = {
        "load_skill": declaration(
            "load_skill",
            context.skill_description,
            {
                "name": {"type": "string", "enum": [SKILL_NAME]},
            },
        ),
        "read_module": declaration(
            "read_module",
            "Read the selected module and existing tests as source data.",
            {
                "module": {"type": "string", "enum": [context.module]},
            },
        ),
        "submit_test_plan": {
            "name": "submit_test_plan",
            "description": "Submit the final evidence-backed test plan.",
            "parametersJsonSchema": TestPlan.model_json_schema(),
        },
    }
    contents = [
        {
            "role": "user",
            "parts": [{"text": f"Plan tests for module {context.module}."}],
        }
    ]
    completed, rejected_plans = [], 0
    for _ in range(MAX_ROUNDS):
        # Discovery tools are each usable once. Submission is exposed only after both.
        allowed = [
            name for name in ("load_skill", "read_module") if name not in completed
        ]
        if not allowed:
            allowed = ["submit_test_plan"]
        content = client.generate(
            context.instructions, contents, [definitions[name] for name in allowed]
        )
        parts = content.get("parts", [])
        if not isinstance(parts, list) or any(
            not isinstance(part, dict) for part in parts
        ):
            raise AgentError("Model response contains malformed parts.")
        calls = [part["functionCall"] for part in parts if "functionCall" in part]
        if not calls or len(calls) > len(allowed):
            raise AgentError("Model must request only the available tools.")
        names = [call.get("name") for call in calls if isinstance(call, dict)]
        if (
            len(names) != len(calls)
            or any(name not in allowed for name in names)
            or len(set(names)) != len(names)
        ):
            raise AgentError(
                "Model requested an unknown, repeated or out-of-sequence tool."
            )
        # Preserve the original model content, including opaque thought signatures.
        contents.append(content)
        results = []
        for call in calls:
            name, args = call["name"], call.get("args")
            if name == "load_skill":
                if args != {"name": SKILL_NAME}:
                    raise AgentError(
                        "Only the registered analysis skill can be loaded."
                    )
                result = {"name": SKILL_NAME, "instructions": context.skill}
            elif name == "read_module":
                if args != {"module": context.module}:
                    raise AgentError(
                        "The module tool accepts only the caller-selected module."
                    )
                result = context.module_data()
                result["configured_boolean_properties"] = [
                    {"attribute": reference, "expected": value}
                    for reference, value in boolean_properties(context.files)
                ]
            else:
                try:
                    plan = TestPlan.model_validate(args)
                except ValidationError as error:
                    rejected_plans += 1
                    if rejected_plans >= 2:
                        raise AgentError(
                            "Model submitted two plans that violate the result contract."
                        ) from None
                    result = {
                        "accepted": False,
                        "errors": [
                            {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                            for e in error.errors(
                                include_input=False, include_url=False
                            )[:4]
                        ],
                        "instruction": "Correct the contract once. no_change requires an empty test_cases list; existing coverage belongs in summary.",
                    }
                else:
                    context.validate_evidence(plan)
                    return {
                        "mode": "analysis_only",
                        "module": context.module,
                        "model": MODEL,
                        "tests_executed": False,
                        "tool_calls": completed + [name],
                        "existing_test_files": [
                            path
                            for path in context.files
                            if path.endswith(".tftest.hcl")
                        ],
                        "provenance": context.provenance(),
                        "usage": dict(client.usage),
                        "plan": plan.model_dump(),
                    }
            completed.append(name)
            response = {"name": name, "response": result}
            if "id" in call:
                response["id"] = call["id"]
            results.append({"functionResponse": response})
        contents.append({"role": "user", "parts": results})
    raise AgentError("Planner reached its four-request limit without a valid plan.")
