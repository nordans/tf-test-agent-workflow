"""Provider-schema-based policy for bounded, plan-only module test suites."""

import json
import math
import re
from dataclasses import dataclass

from test_agent.hcl import parse_hcl, require

NAME = r"[A-Za-z_][A-Za-z0-9_-]{0,63}"
TEST_PATH = re.compile(r"tests/[A-Za-z0-9_][A-Za-z0-9_.-]{0,90}\.tftest\.hcl")
PROVIDERS = {"azurerm", "external"}
FUNCTIONS = frozenset(
    "length alltrue anytrue can try toset tolist tomap tostring tonumber sort keys "
    "values contains startswith endswith lower upper lookup flatten concat merge "
    "replace distinct compact coalesce coalescelist jsonencode jsondecode regex "
    "regexall cidrnetmask cidrhost cidrsubnet setunion setintersection setsubtract "
    "slice join split format formatlist trimspace trimprefix trimsuffix min max "
    "abs ceil floor sum range zipmap reverse one signum strcontains".split()
)
SOURCE_FUNCTIONS = FUNCTIONS | {"list", "set", "map", "object", "optional", "tuple"}


def parse(text, *, source=False):
    return parse_hcl(text, allowed_functions=SOURCE_FUNCTIONS if source else FUNCTIONS)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def literal(value, depth=0):
    if depth > 12:
        return False
    if type(value) is float:
        return math.isfinite(value)
    if value is None or type(value) in (bool, int):
        return True
    if isinstance(value, str):
        return len(value) <= 1000 and "$" + "{" not in value and "%{" not in value
    if isinstance(value, list):
        return len(value) <= 64 and all(literal(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return len(value) <= 64 and all(
            isinstance(k, str) and literal(k, depth + 1) and literal(v, depth + 1)
            for k, v in value.items()
        )
    return False


def blocks(data, key):
    result = {}
    for block in data.get(key, []):
        require(isinstance(block, dict) and len(block) == 1, "INVALID_BLOCK")
        kind, names = next(iter(block.items()))
        require(isinstance(names, dict), "INVALID_BLOCK")
        for name, body in names.items():
            require(
                re.fullmatch(NAME, kind)
                and re.fullmatch(NAME, name)
                and isinstance(body, dict)
                and (kind, name) not in result,
                "INVALID_OR_DUPLICATE_BLOCK",
            )
            result[kind, name] = body
    return result


@dataclass
class Source:
    data: dict
    providers: set[str]
    resources: dict
    data_sources: dict
    variables: dict
    outputs: set[str]


def validate_sources(files):
    merged = {}
    for path, content in files.items():
        if not path.endswith(".tf"):
            continue
        data = parse(content, source=True)
        require(
            set(data)
            <= {"terraform", "resource", "data", "variable", "output", "locals"},
            "UNSUPPORTED_SOURCE_BLOCK",
        )
        for key, items in data.items():
            require(isinstance(items, list), "INVALID_SOURCE_BLOCK")
            merged.setdefault(key, []).extend(items)
    configs = merged.get("terraform", [])
    require(len(configs) == 1, "EXPECTED_ONE_TERRAFORM_BLOCK")
    config = configs[0]
    require(
        set(config) <= {"required_version", "required_providers"},
        "BACKEND_OR_CLOUD_NOT_ALLOWED",
    )
    declarations = config.get("required_providers", [])
    require(
        len(declarations) == 1 and bool(declarations[0]), "EXPLICIT_PROVIDERS_REQUIRED"
    )
    providers = set(declarations[0])
    require(providers <= PROVIDERS, "UNAPPROVED_PROVIDER")
    for name, settings in declarations[0].items():
        require(
            isinstance(settings, dict)
            and set(settings) == {"source", "version"}
            and settings["source"] == f"hashicorp/{name}"
            and isinstance(settings["version"], str),
            "UNAPPROVED_PROVIDER_DECLARATION",
        )
    resources, data_sources = blocks(merged, "resource"), blocks(merged, "data")
    require(bool(resources or data_sources), "NO_TESTABLE_RESOURCES")
    for kind, _ in resources:
        require(
            kind.startswith("azurerm_") and "azurerm" in providers,
            "UNAPPROVED_RESOURCE_PROVIDER",
        )
    for kind, _ in data_sources:
        require(
            (kind.startswith("azurerm_") and "azurerm" in providers)
            or (kind == "external" and "external" in providers),
            "UNAPPROVED_DATA_PROVIDER",
        )

    def safe_body(value):
        if isinstance(value, dict):
            require(
                not {"provisioner", "connection", "provider"} & set(value),
                "EXECUTION_OR_PROVIDER_OVERRIDE_NOT_ALLOWED",
            )
            for child in value.values():
                safe_body(child)
        elif isinstance(value, list):
            for child in value:
                safe_body(child)

    for body in [*resources.values(), *data_sources.values()]:
        safe_body(body)
    variables = {}
    for item in merged.get("variable", []):
        require(isinstance(item, dict) and len(item) == 1, "INVALID_VARIABLE")
        for name, body in item.items():
            require(
                name not in variables and re.fullmatch(NAME, name), "INVALID_VARIABLE"
            )
            variables[name] = body
    return Source(
        merged,
        providers,
        resources,
        data_sources,
        variables,
        {name for item in merged.get("output", []) for name in item},
    )


def provider_schema(schemas, kind, *, data=False):
    provider = "external" if kind == "external" else "azurerm"
    try:
        return schemas[f"registry.terraform.io/hashicorp/{provider}"][
            "data_source_schemas" if data else "resource_schemas"
        ][kind]["block"]
    except KeyError:
        require(False, "TYPE_NOT_AVAILABLE_IN_APPROVED_RUNTIME")


def computed_defaults(defaults, schema, prefix="defaults"):
    require(
        isinstance(defaults, dict) and literal(defaults),
        "MOCK_DEFAULTS_MUST_BE_LITERALS",
    )
    for name, value in defaults.items():
        field = f"{prefix}.{name}"
        attribute = schema.get("attributes", {}).get(name)
        if attribute:
            require(
                attribute.get("computed") is True
                and (not attribute.get("optional") or name == "id")
                and not attribute.get("required"),
                f"ONLY_COMPUTED_ATTRIBUTES_CAN_BE_MOCKED: {field}",
            )
        elif name in schema.get("block_types", {}):
            require(
                isinstance(value, dict),
                f"NESTED_MOCK_DEFAULTS_MUST_BE_OBJECT: {field}; supply one object for all block instances, not a list",
            )
            computed_defaults(value, schema["block_types"][name]["block"], field)
        else:
            require(False, f"UNKNOWN_MOCK_ATTRIBUTE: {field}")


def validate_mocks(data, source, schemas):
    mocks = {}
    for item in data.get("mock_provider", []):
        require(isinstance(item, dict) and len(item) == 1, "INVALID_MOCK_PROVIDER")
        name, body = next(iter(item.items()))
        require(
            name in source.providers and name not in mocks, "UNEXPECTED_MOCK_PROVIDER"
        )
        require(
            set(body) <= {"override_during", "mock_resource", "mock_data"},
            "UNSUPPORTED_MOCK_CONFIGURATION",
        )
        require(
            body.get("override_during", "plan") == "plan",
            "MOCK_MUST_OVERRIDE_DURING_PLAN",
        )
        for key in ("mock_resource", "mock_data"):
            seen = set()
            for mock in body.get(key, []):
                require(isinstance(mock, dict) and len(mock) == 1, "INVALID_MOCK")
                kind, config = next(iter(mock.items()))
                provider = "external" if kind == "external" else "azurerm"
                require(provider == name and kind not in seen, "INVALID_MOCK_TYPE")
                seen.add(kind)
                require(set(config) == {"defaults"}, "UNSUPPORTED_MOCK_CONFIGURATION")
                computed_defaults(
                    config["defaults"],
                    provider_schema(schemas, kind, data=key == "mock_data"),
                    kind,
                )
                # Legacy SDK schemas mark the computed resource ID Optional.
                # Its exception must never mask an explicitly configured ID.
                instances = (
                    source.data_sources if key == "mock_data" else source.resources
                )
                if "id" in config["defaults"]:
                    require(
                        not any(
                            "id" in instance
                            for (resource_type, _), instance in instances.items()
                            if resource_type == kind
                        ),
                        "CONFIGURED_ID_CANNOT_BE_MOCKED",
                    )
        mocks[name] = body
    require(set(mocks) == source.providers, "EVERY_PROVIDER_MUST_BE_MOCKED")
    return mocks


@dataclass
class Suite:
    files: dict[str, str]
    runs: tuple[tuple[str, str], ...]
    parsed: dict


def test_files(files, module):
    prefix = f"modules/{module}/"
    return {
        path.removeprefix(prefix): text
        for path, text in files.items()
        if path.startswith(prefix) and path.endswith(".tftest.hcl")
    }


def validate_suite(sources, tests, schemas):
    source = validate_sources(sources)
    for kind, _ in source.resources:
        provider_schema(schemas, kind)
    for kind, _ in source.data_sources:
        provider_schema(schemas, kind, data=True)
    require(isinstance(tests, dict) and 1 <= len(tests) <= 8, "EXPECTED_TEST_FILES")
    require(
        len({p.casefold() for p in tests}) == len(tests), "CASE_COLLIDING_TEST_PATHS"
    )
    parsed, names = {}, []
    for path, text in sorted(tests.items()):
        require(TEST_PATH.fullmatch(path) is not None, "UNAPPROVED_TEST_PATH")
        data = parse(text)
        require(
            set(data) <= {"mock_provider", "variables", "run"},
            "UNSUPPORTED_TEST_BLOCK: only mock_provider, variables and run are top-level blocks; put mock_resource and mock_data inside mock_provider",
        )
        mocks = validate_mocks(data, source, schemas)
        requires_plan_mocks = False
        globals_ = data.get("variables", [])
        require(
            len(globals_) <= 1
            and all(literal(v) and set(v) <= set(source.variables) for v in globals_),
            "INVALID_TEST_INPUTS",
        )
        runs = data.get("run", [])
        require(1 <= len(runs) <= 12, "EXPECTED_TEST_RUNS")
        local_names = set()
        for run in runs:
            require(isinstance(run, dict) and len(run) == 1, "INVALID_RUN")
            name, body = next(iter(run.items()))
            require(
                re.fullmatch(NAME, name) and name not in local_names, "INVALID_RUN_NAME"
            )
            local_names.add(name)
            require(
                set(body) <= {"command", "variables", "assert", "expect_failures"},
                "UNSUPPORTED_RUN_ARGUMENT",
            )
            require(body.get("command") == "plan", "RUN_MUST_EXPLICITLY_PLAN")
            inputs = body.get("variables", [])
            require(
                len(inputs) <= 1
                and all(literal(v) and set(v) <= set(source.variables) for v in inputs),
                "INVALID_TEST_INPUTS",
            )
            assertions, failures = (
                body.get("assert", []),
                body.get("expect_failures", []),
            )
            require(
                0 <= len(assertions) <= 12 and (assertions or failures),
                "EXPECTED_ASSERTIONS: each run allows at most 12 assertions and needs an assertion or a real validation expectation; split checks across runs instead of removing them",
            )
            for failure in failures:
                match = (
                    re.fullmatch(r"\$\{var\.(" + NAME + r")\}", failure)
                    if isinstance(failure, str)
                    else None
                )
                require(
                    match and source.variables.get(match[1], {}).get("validation"),
                    "EXPECTED_FAILURE_MUST_REFERENCE_VALIDATION",
                )
            for assertion in assertions:
                require(
                    set(assertion) == {"condition", "error_message"},
                    "INVALID_ASSERTION",
                )
                condition = assertion["condition"]
                require(
                    isinstance(condition, str)
                    and condition.startswith("$" + "{")
                    and isinstance(assertion["error_message"], str),
                    "INVALID_ASSERTION",
                )
                expression = re.sub(r'"(?:[^"\\]|\\.)*"', '""', condition)
                refs = [re.escape(f"{kind}.{name}") for kind, name in source.resources]
                refs.extend(
                    re.escape(f"data.{kind}.{name}")
                    for kind, name in source.data_sources
                )
                refs.extend(re.escape(f"output.{name}") for name in source.outputs)
                require(
                    refs and re.search(r"\b(?:" + "|".join(refs) + r")\b", expression),
                    "ASSERTION_MUST_REFERENCE_MODULE",
                )
                if re.search(r"\boutput\.", expression):
                    requires_plan_mocks = True
                for kind, resource_name in source.resources:
                    schema = provider_schema(schemas, kind)
                    for attribute, settings in schema.get("attributes", {}).items():
                        if (
                            settings.get("computed")
                            and attribute not in source.resources[kind, resource_name]
                            and re.search(
                                re.escape(f"{kind}.{resource_name}")
                                + r"(?:\[[^\]]+\])?\."
                                + re.escape(attribute)
                                + r"\b",
                                expression,
                            )
                        ):
                            requires_plan_mocks = True
            names.append((path, name))
        parsed[path] = data
        if requires_plan_mocks:
            require(
                all(body.get("override_during") == "plan" for body in mocks.values()),
                "OUTPUT_AND_COMPUTED_CHECKS_REQUIRE_PLAN_MOCKS: set override_during = plan inside each mock_provider; keep the output checks",
            )
    return Suite(dict(tests), tuple(names), parsed)


def preserve_mock_values(old, new):
    def contains(previous, candidate):
        if isinstance(previous, dict):
            return (
                isinstance(candidate, dict)
                and set(previous) <= set(candidate)
                and all(
                    contains(value, candidate[key]) for key, value in previous.items()
                )
            )
        if isinstance(previous, list):
            return isinstance(candidate, list) and all(
                any(contains(value, item) for item in candidate) for value in previous
            )
        return previous == candidate

    require(contains(old, new), "EXISTING_MOCKS_MUST_BE_PRESERVED")


def required_outputs(before, after, plan):
    outputs = validate_sources(after).outputs
    if not before or any(
        case.category == "output" for case in (plan.test_cases if plan else [])
    ):
        return outputs
    return outputs - validate_sources(before).outputs


def validate_output_coverage(before, after, suite, plan):
    required = required_outputs(before, after, plan)
    observed = set()
    for data in suite.parsed.values():
        for run in data["run"]:
            for body in run.values():
                for assertion in body.get("assert", []):
                    expression = re.sub(
                        r'"(?:[^"\\]|\\.)*"', '""', assertion["condition"]
                    )
                    observed.update(
                        re.findall(r"\boutput\.(" + NAME + r")\b", expression)
                    )
    missing = sorted(required - observed)
    require(not missing, "PLANNED_OUTPUT_ASSERTIONS_MISSING: " + ", ".join(missing[:5]))


def allowed_expectations(before, after):
    old, new = validate_sources(before), validate_sources(after)
    replacements = {}
    for address, body in old.resources.items():
        for attr, value in body.items():
            new_value = new.resources.get(address, {}).get(attr)
            variable = (
                re.fullmatch(r"\$\{var\.(" + NAME + r")\}", value)
                if isinstance(value, str)
                else None
            )
            var_name = None
            if variable and value == new_value:
                var_name = variable[1]
                old_var, new_var = (
                    old.variables.get(var_name, {}),
                    new.variables.get(var_name, {}),
                )
                if "default" not in old_var or "default" not in new_var:
                    continue
                value, new_value = old_var["default"], new_var["default"]
            if (
                type(value) not in (int, float, str)
                or type(new_value) is not type(value)
                or value == new_value
                or not literal(value)
                or not literal(new_value)
            ):
                continue
            ref = ".".join((*address, attr))
            old_expr = "$" + "{" + f"{ref} == {json.dumps(value)}" + "}"
            new_expr = "$" + "{" + f"{ref} == {json.dumps(new_value)}" + "}"
            replacements[old_expr] = (new_expr, var_name)
    return replacements


def validate_update(
    before_sources,
    after_sources,
    previous,
    candidate,
    schemas,
    *,
    previous_sources=None,
):
    result = validate_suite(after_sources, candidate, schemas)
    if not previous:
        return result
    old = validate_suite(
        previous_sources
        if previous_sources is not None
        else before_sources or after_sources,
        previous,
        schemas,
    )
    replacements = (
        allowed_expectations(before_sources, after_sources) if before_sources else {}
    )
    added_variables = set(validate_sources(after_sources).variables) - (
        set(validate_sources(before_sources).variables) if before_sources else set()
    )

    def preserve_inputs(old_inputs, new_inputs):
        old_values = {k: v for item in old_inputs for k, v in item.items()}
        new_values = {k: v for item in new_inputs for k, v in item.items()}
        require(
            set(old_values) <= set(new_values)
            and set(new_values) - set(old_values) <= added_variables
            and all(
                canonical(value) == canonical(new_values[key])
                for key, value in old_values.items()
            ),
            "EXISTING_INPUTS_MUST_BE_PRESERVED",
        )

    for path, data in old.parsed.items():
        require(path in result.parsed, "EXISTING_TEST_FILES_MUST_BE_PRESERVED")
        updated = result.parsed[path]
        preserve_mock_values(
            data.get("mock_provider", []), updated.get("mock_provider", [])
        )
        preserve_inputs(data.get("variables", []), updated.get("variables", []))
        new_runs = {name: body for run in updated["run"] for name, body in run.items()}
        for run in data["run"]:
            name, body = next(iter(run.items()))
            require(name in new_runs, "EXISTING_RUNS_MUST_BE_PRESERVED")
            current = new_runs[name]
            preserve_inputs(body.get("variables", []), current.get("variables", []))
            require(
                canonical(body.get("expect_failures", []))
                == canonical(current.get("expect_failures", [])),
                "EXISTING_EXPECTED_FAILURES_MUST_BE_PRESERVED",
            )
            inputs = {
                k
                for item in [*data.get("variables", []), *body.get("variables", [])]
                for k in item
            }
            conditions = {a["condition"] for a in current.get("assert", [])}
            for assertion in body.get("assert", []):
                condition = assertion["condition"]
                if condition in replacements:
                    replacement, variable = replacements[condition]
                    if variable not in inputs:
                        condition = replacement
                require(
                    condition in conditions, "EXISTING_ASSERTIONS_MUST_BE_PRESERVED"
                )
    return result
