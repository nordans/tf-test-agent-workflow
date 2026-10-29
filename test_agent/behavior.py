"""Source-derived Boolean checks and nested resource attribute locations.

These are bounded structural checks, not a semantic coverage measurement.
"""

import re
from dataclasses import dataclass

import hcl2

from test_agent.context import AgentError
from test_agent.hcl import require
from test_agent.terraform_policy import validate_output_coverage
from test_agent.validation_cases import validate_validation_coverage


@dataclass(frozen=True)
class Attribute:
    file: str
    address: str
    path: str
    expression: str
    start: int
    end: int

    @property
    def reference(self):
        return f"{self.address}.{self.path}"


def walk_attributes(path, text, address, body, parents=(), dynamic=False):
    for item in body.children:
        if getattr(item, "data", None) == "attribute":
            key, expression = str(item.children[0].children[0]), item.children[2]
            start, end = expression.meta.start_pos, expression.meta.end_pos
            yield Attribute(
                path, address, ".".join((*parents, key)), text[start:end], start, end
            )
        elif getattr(item, "data", None) == "block":
            name = str(item.children[0].children[0])
            if name == "dynamic":
                label = item.children[1]
                name = text[label.meta.start_pos : label.meta.end_pos].strip('"')
            nested = parents if name == "content" and dynamic else (*parents, name)
            for child in item.children:
                if getattr(child, "data", None) == "body":
                    is_dynamic = str(item.children[0].children[0]) == "dynamic"
                    yield from walk_attributes(
                        path, text, address, child, nested, is_dynamic
                    )


def resource_attributes(files):
    for path, text in sorted(files.items()):
        if not path.endswith(".tf"):
            continue
        tree = hcl2.parses_to_tree(text)
        for block in tree.find_data("block"):
            if str(block.children[0].children[0]) != "resource":
                continue
            address = ".".join(
                text[node.meta.start_pos : node.meta.end_pos].strip('"')
                for node in block.children[1:3]
            )

            for child in block.children:
                if getattr(child, "data", None) == "body":
                    yield from walk_attributes(path, text, address, child)


def normalize_reference(expression):
    # Ignore instance indices; nested block names remain part of the traversal.
    expression = re.sub(r'\[(?:[^\]"\\]|"(?:[^"\\]|\\.)*")*\]', "", expression)
    for _ in range(4):
        expression = re.sub(r"\bone\(([^()]*)\)", r"\1", expression)
    return re.sub(r"\s+", "", expression)


def boolean_properties(files):
    properties = sorted(
        {
            (item.reference, item.expression == "true")
            for item in resource_attributes(files)
            if item.expression in ("true", "false")
        }
    )
    require(len(properties) <= 64, "CONFIGURED_BOOLEAN_BUDGET_EXCEEDED")
    return properties


def required_booleans(before, after):
    previous = set(boolean_properties(before))
    return [
        {"attribute": reference, "expected": value}
        for reference, value in boolean_properties(after)
        if (reference, value) not in previous
    ]


def validate_boolean_coverage(before, after, suite):
    observed = set()
    for data in suite.parsed.values():
        for run in data["run"]:
            for body in run.values():
                for assertion in body.get("assert", []):
                    condition = assertion["condition"][2:-1].strip()
                    condition = normalize_reference(condition)
                    # Require a direct expectation; mentioning a flag in an OR/tautology
                    # does not satisfy this structural safeguard.
                    match = re.fullmatch(r"([A-Za-z_][\w.-]*)==(true|false)", condition)
                    if match:
                        observed.add((match[1], match[2] == "true"))
    missing = [
        f"{item['attribute']} == {str(item['expected']).lower()}"
        for item in required_booleans(before, after)
        if (item["attribute"], item["expected"]) not in observed
    ]
    require(
        not missing, "CONFIGURED_BOOLEAN_ASSERTIONS_MISSING: " + "; ".join(missing[:8])
    )


def validate_required_coverage(before, after, suite, plan):
    errors = []
    for check in (
        lambda: validate_output_coverage(before, after, suite, plan),
        lambda: validate_boolean_coverage(before, after, suite),
        lambda: validate_validation_coverage(before, after, suite),
    ):
        try:
            check()
        except AgentError as error:
            errors.append(str(error)[:300])
    if errors:
        raise AgentError("\n".join(errors))
