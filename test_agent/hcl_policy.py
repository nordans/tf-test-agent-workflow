"""A deliberately narrow HCL policy for mocked log-analytics-workspace tests."""

import json
from dataclasses import dataclass

import hcl2
from lark.exceptions import LarkError

from test_agent.context import AgentError

MODULE = "log-analytics-workspace"
RESOURCE = "azurerm_log_analytics_workspace"
TEST_PATH = "tests/generated.tftest.hcl"
MAX_HCL_BYTES = 32_768
OPTIONS = hcl2.SerializationOptions(
    with_comments=False, explicit_blocks=False, strip_string_quotes=True
)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AgentError(f"HCL policy rejected proposal: {code}.")


def parse_hcl(text: str, *, allowed_functions=frozenset({"length"})) -> dict:
    require(len(text.encode("utf-8")) <= MAX_HCL_BYTES, "FILE_TOO_LARGE")
    try:
        tree = hcl2.parses_to_tree(text)
        for node in tree.iter_subtrees():
            if "function" in str(node.data):
                require(str(node.data) == "function_call", "UNSUPPORTED_FUNCTION")
                name = str(node.children[0].children[0])
                require(name in allowed_functions, "UNSUPPORTED_FUNCTION")
        return hcl2.loads(text, serialization_options=OPTIONS)
    except (
        LarkError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        RecursionError,
    ):
        raise AgentError("HCL policy rejected proposal: INVALID_SYNTAX.") from None


def one_block(blocks, label: str, code: str) -> dict:
    require(isinstance(blocks, list) and len(blocks) == 1, code)
    block = blocks[0]
    require(isinstance(block, dict) and set(block) == {label}, code)
    body = block[label]
    require(isinstance(body, dict), code)
    return body


def variables(body) -> None:
    require(isinstance(body, dict), "INVALID_VARIABLES")
    require(
        set(body)
        <= {"name", "resource_group_name", "location", "retention_in_days", "tags"},
        "UNKNOWN_VARIABLE",
    )
    for name, value in body.items():
        if name == "tags":
            require(isinstance(value, dict) and len(value) <= 16, "INVALID_TAGS")
            values = [*value.keys(), *value.values()]
        elif name == "retention_in_days":
            require(type(value) is int, "RETENTION_MUST_BE_LITERAL_INTEGER")
            continue
        else:
            values = [value]
        require(
            all(
                isinstance(item, str)
                and len(item) <= 200
                and "${" not in item
                and "%{" not in item
                for item in values
            ),
            "VARIABLES_MUST_BE_LITERALS",
        )


def validate_sources(files: dict[str, str]) -> None:
    """Reject execution/provider features outside this milestone's source subset."""
    merged = {}
    for path, text in files.items():
        if path.endswith(".tftest.hcl"):
            continue
        require(path.endswith(".tf"), "UNSUPPORTED_SOURCE_FILE")
        data = parse_hcl(text, allowed_functions=frozenset({"length", "map"}))
        require(
            set(data) <= {"terraform", "resource", "variable", "output"},
            "UNSUPPORTED_SOURCE_BLOCK",
        )
        for key, blocks in data.items():
            require(isinstance(blocks, list), "UNSUPPORTED_SOURCE_SHAPE")
            merged.setdefault(key, []).extend(blocks)
    config = merged.get("terraform", [])
    require(len(config) == 1, "EXPECTED_ONE_TERRAFORM_BLOCK")
    config = config[0]
    require(isinstance(config, dict), "INVALID_TERRAFORM_BLOCK")
    require(
        set(config) <= {"required_version", "required_providers"},
        "BACKEND_OR_CLOUD_NOT_ALLOWED",
    )
    providers = config.get("required_providers")
    require(isinstance(providers, list) and len(providers) == 1, "INVALID_PROVIDERS")
    require(
        isinstance(providers[0], dict) and set(providers[0]) == {"azurerm"},
        "ONLY_AZURERM_ALLOWED",
    )
    provider = providers[0]["azurerm"]
    require(
        isinstance(provider, dict) and set(provider) == {"source", "version"},
        "INVALID_PROVIDER",
    )
    require(
        provider["source"] == "hashicorp/azurerm" and provider["version"] == "~> 4.0",
        "UNAPPROVED_PROVIDER",
    )
    resource = one_block(
        merged.get("resource"), RESOURCE, "EXPECTED_WORKSPACE_RESOURCE"
    )
    require(set(resource) == {"this"}, "EXPECTED_WORKSPACE_RESOURCE")
    require(isinstance(resource["this"], dict), "EXPECTED_WORKSPACE_RESOURCE")
    require(
        set(resource["this"])
        == {
            "name",
            "resource_group_name",
            "location",
            "sku",
            "retention_in_days",
            "tags",
        },
        "UNSUPPORTED_RESOURCE_ARGUMENT",
    )


@dataclass(frozen=True)
class Proposal:
    hcl: str
    runs: tuple[str, ...]
    conditions: tuple[str, ...]


def validate_proposal(text: str) -> Proposal:
    data = parse_hcl(text)
    require(
        set(data) <= {"mock_provider", "variables", "run"}, "UNSUPPORTED_TEST_BLOCK"
    )
    mock = one_block(data.get("mock_provider"), "azurerm", "AZURERM_MUST_BE_MOCKED")
    require(
        set(mock) == {"override_during", "mock_resource"}, "INVALID_MOCK_CONFIGURATION"
    )
    require(mock["override_during"] == "plan", "MOCK_MUST_OVERRIDE_DURING_PLAN")
    resource = one_block(mock["mock_resource"], RESOURCE, "INVALID_MOCK_RESOURCE")
    require(set(resource) == {"defaults"}, "INVALID_MOCK_RESOURCE")
    require(
        isinstance(resource["defaults"], dict) and set(resource["defaults"]) == {"id"},
        "ONLY_COMPUTED_ID_CAN_BE_MOCKED",
    )
    mock_id = resource["defaults"]["id"]
    require(
        isinstance(mock_id, str)
        and mock_id.startswith("/subscriptions/")
        and len(mock_id) <= 300
        and "${" not in mock_id,
        "INVALID_MOCK_ID",
    )
    global_variables = data.get("variables", [])
    require(
        isinstance(global_variables, list) and len(global_variables) == 1,
        "EXPECTED_VARIABLES",
    )
    variables(global_variables[0])
    require(
        {"name", "resource_group_name", "location"} <= set(global_variables[0]),
        "MISSING_REQUIRED_VARIABLES",
    )
    runs = data.get("run", [])
    require(isinstance(runs, list) and 1 <= len(runs) <= 6, "EXPECTED_TEST_RUNS")
    names, conditions = [], []
    for run in runs:
        require(isinstance(run, dict) and len(run) == 1, "INVALID_RUN")
        name, body = next(iter(run.items()))
        require(
            isinstance(name, str)
            and name.isascii()
            and name.replace("_", "").isalnum()
            and len(name) <= 64
            and name not in names,
            "INVALID_RUN_NAME",
        )
        require(
            isinstance(body, dict) and set(body) <= {"command", "variables", "assert"},
            "UNSUPPORTED_RUN_ARGUMENT",
        )
        require(body.get("command") == "plan", "RUN_MUST_EXPLICITLY_PLAN")
        if "variables" in body:
            require(
                isinstance(body["variables"], list) and len(body["variables"]) == 1,
                "INVALID_VARIABLES",
            )
            variables(body["variables"][0])
        assertions = body.get("assert", [])
        require(
            isinstance(assertions, list) and 1 <= len(assertions) <= 8,
            "EXPECTED_ASSERTIONS",
        )
        for assertion in assertions:
            require(
                isinstance(assertion, dict)
                and set(assertion) == {"condition", "error_message"},
                "INVALID_ASSERTION",
            )
            condition = assertion["condition"]
            require(
                isinstance(condition, str)
                and condition.startswith("${")
                and (f"{RESOURCE}.this." in condition or "output.id" in condition),
                "ASSERTION_MUST_REFERENCE_MODULE",
            )
            require(
                isinstance(assertion["error_message"], str)
                and len(assertion["error_message"]) <= 500,
                "INVALID_ERROR_MESSAGE",
            )
            # Preserve the condition together with its inputs and run identity.
            # Keeping just the expression would permit weakening a test by changing inputs.
            conditions.append(
                json.dumps(
                    {
                        "run": name,
                        "condition": condition,
                        "global_variables": global_variables,
                        "run_variables": body.get("variables", []),
                        "mock": mock,
                    },
                    sort_keys=True,
                )
            )
        names.append(name)
    return Proposal(text, tuple(names), tuple(conditions))
