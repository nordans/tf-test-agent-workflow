"""Concrete examples for a deliberately small subset of source validations."""

import math
import re

import hcl2

from test_agent.hcl import OPTIONS, require


def variables(files):
    return {
        name: body
        for path, text in files.items()
        if path.endswith(".tf")
        for item in hcl2.loads(text, serialization_options=OPTIONS).get("variable", [])
        for name, body in item.items()
    }


def condition_examples(name, conditions):
    """Return guaranteed invalid inputs and, for exact numeric rules, valid bounds."""
    examples, lower, upper, integer, recognized = [], [], [], False, True
    variable = re.escape("var." + name)
    for condition in conditions:
        expression = re.sub(r"\s+", "", condition[2:-1])
        if expression in (
            f"length(trimspace(var.{name}))>0",
            f"length(trimspace(var.{name}))>=1",
        ):
            examples.extend([(False, ""), (False, "   ")])
            recognized = False  # Do not infer positive text examples.
            continue
        if expression in (f"length(var.{name})>0", f"length(var.{name})>=1"):
            examples.append((False, ""))
            recognized = False
            continue
        for term in expression.split("&&"):
            if term in (
                f"floor(var.{name})==var.{name}",
                f"var.{name}==floor(var.{name})",
            ):
                integer = True
                continue
            match = re.fullmatch(variable + r"(>=|>|<=|<)(-?\d+(?:\.\d+)?)", term)
            if not match:
                recognized = False
                continue
            operation, text = match.groups()
            value = float(text)
            if not math.isfinite(value) or abs(value) > 1_000_000_000:
                recognized = False
                continue
            if operation.startswith(">"):
                lower.append((value, operation == ">"))
                examples.append((False, value if operation == ">" else value - 1))
            else:
                upper.append((value, operation == "<"))
                examples.append((False, value if operation == "<" else value + 1))
    if integer:
        low = max(lower, default=(0.0, False))
        examples.append((False, math.ceil(low[0]) + 0.5))
    # Unknown clauses can invalidate otherwise valid boundaries. Never invent a
    # positive example for a condition we do not fully understand.
    if recognized and lower and upper:
        low, high = max(lower), min(upper, key=lambda x: (x[0], not x[1]))
        if integer:
            minimum = math.floor(low[0]) + 1 if low[1] else math.ceil(low[0])
            maximum = math.ceil(high[0]) - 1 if high[1] else math.floor(high[0])
        else:
            minimum = low[0] + (0.5 if low[1] else 0)
            maximum = high[0] - (0.5 if high[1] else 0)
        if minimum <= maximum:
            examples.extend([(True, minimum), (True, maximum)])
    return examples


def required_validation_examples(before, after):
    previous, current = variables(before), variables(after)
    result, seen = [], set()
    for name, body in sorted(current.items()):
        validations = body.get("validation", [])
        conditions = [item["condition"] for item in validations]
        previous_conditions = [
            item["condition"] for item in previous.get(name, {}).get("validation", [])
        ]
        if not conditions or [re.sub(r"\s+", "", c) for c in conditions] == [
            re.sub(r"\s+", "", c) for c in previous_conditions
        ]:
            continue
        # Conditions with alternatives/negation do not make each comparison a
        # necessary constraint. They stay under model/human review.
        if any("||" in item or "?" in item for item in conditions):
            continue
        for valid, value in condition_examples(name, conditions):
            key = (name, valid, value)
            if key not in seen:
                seen.add(key)
                result.append({"variable": name, "value": value, "valid": valid})
    require(len(result) <= 32, "VALIDATION_EXAMPLE_BUDGET_EXCEEDED")
    return result


def validate_validation_coverage(before, after, suite):
    defaults = {k: v["default"] for k, v in variables(after).items() if "default" in v}
    observed = []
    for data in suite.parsed.values():
        global_inputs = {
            k: v for item in data.get("variables", []) for k, v in item.items()
        }
        for run in data["run"]:
            body = next(iter(run.values()))
            inputs = {
                **defaults,
                **global_inputs,
                **{k: v for item in body.get("variables", []) for k, v in item.items()},
            }
            failures = body.get("expect_failures", [])
            observed.append((inputs, failures))
    missing = []
    for example in required_validation_examples(before, after):
        name, value, valid = example["variable"], example["value"], example["valid"]
        if not any(
            name in inputs
            and type(inputs[name]) is not bool
            and inputs[name] == value
            and (not failures if valid else "${var." + name + "}" in failures)
            for inputs, failures in observed
        ):
            missing.append(
                f"{name}={value!r} ({'valid' if valid else 'expect failure'})"
            )
    require(not missing, "VALIDATION_EXAMPLES_MISSING: " + "; ".join(missing[:10]))
