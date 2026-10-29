"""Do not accept promises of validation coverage without concrete test inputs."""

from types import SimpleNamespace

import hcl2
import pytest

from test_agent.context import AgentError
from test_agent.hcl import OPTIONS
from test_agent.validation_cases import (
    required_validation_examples,
    validate_validation_coverage,
)


def files(condition, name="count"):
    return {
        "module.tf": f'''variable "{name}" {{
      type = number
      validation {{
        condition = {condition}
        error_message = "Invalid input"
      }}
    }}'''
    }


BOUNDS = "var.count >= 1 && var.count <= 5 && floor(var.count) == var.count"


def suite(values):
    return SimpleNamespace(
        parsed={
            "tests/example.tftest.hcl": {
                "run": [
                    {
                        f"case_{index}": {
                            "variables": [{"count": value}],
                            "expect_failures": [] if valid else ["${var.count}"],
                        }
                    }
                    for index, (valid, value) in enumerate(values)
                ]
            }
        }
    )


def test_bounded_integer_has_both_invalid_limits_fraction_and_valid_boundaries():
    examples = required_validation_examples({}, files(BOUNDS))
    assert {(x["valid"], x["value"]) for x in examples} == {
        (False, 0),
        (False, 6),
        (False, 1.5),
        (True, 1),
        (True, 5),
    }


def test_strict_bounds_test_the_excluded_endpoints():
    source = files("var.count > 1 && var.count < 5 && floor(var.count) == var.count")
    examples = required_validation_examples({}, source)
    assert {(x["valid"], x["value"]) for x in examples} == {
        (False, 1),
        (False, 5),
        (False, 1.5),
        (True, 2),
        (True, 4),
    }


@pytest.mark.parametrize(
    "condition,values",
    [
        ("length(trimspace(var.name)) > 0", {"", "   "}),
        ("length(var.name) > 0", {""}),
    ],
)
def test_whitespace_rejection_is_required_only_when_implemented(condition, values):
    assert {
        x["value"] for x in required_validation_examples({}, files(condition, "name"))
    } == values


def test_alternatives_and_unknown_rules_do_not_invent_requirements():
    assert not required_validation_examples(
        {}, files("var.count >= 1 || var.count <= 5")
    )
    assert not required_validation_examples({}, files("contains([1, 3, 5], var.count)"))
    known_and_unknown = files(
        "var.count >= 1 && var.count <= 5 && contains([2, 4], var.count)"
    )
    assert all(
        not x["valid"] for x in required_validation_examples({}, known_and_unknown)
    )


def test_unchanged_validation_is_not_a_new_requirement():
    source = files(BOUNDS)
    assert required_validation_examples(source, source) == []
    assert required_validation_examples(source, files(BOUNDS.replace("<= 5", "<= 4")))
    message_only = {
        p: text.replace("Invalid input", "More specific error")
        for p, text in source.items()
    }
    assert not required_validation_examples(source, message_only)


def test_one_invalid_example_does_not_satisfy_the_other_branches():
    with pytest.raises(AgentError, match="VALIDATION_EXAMPLES_MISSING"):
        validate_validation_coverage({}, files(BOUNDS), suite([(False, 0)]))
    validate_validation_coverage(
        {},
        files(BOUNDS),
        suite([(False, 0), (False, 6), (False, 1.5), (True, 1), (True, 5)]),
    )


def test_expected_failure_is_bound_to_the_validated_variable():
    incomplete = suite([(False, 0), (False, 6), (False, 1.5), (True, 1), (True, 5)])
    incomplete.parsed["tests/example.tftest.hcl"]["run"][0]["case_0"][
        "expect_failures"
    ] = ["${var.other}"]
    with pytest.raises(AgentError, match="count=0"):
        validate_validation_coverage({}, files(BOUNDS), incomplete)


def test_global_inputs_and_real_hcl_numeric_values_are_supported():
    text = """variables { count = 1 }
    run "min" { command = plan }
    run "max" { command = plan
      variables { count = 5.0 }
    }
    run "low" { command = plan
      variables { count = 0 }
      expect_failures = [var.count]
    }
    run "high" { command = plan
      variables { count = 6 }
      expect_failures = [var.count]
    }
    run "fraction" { command = plan
      variables { count = 1.5 }
      expect_failures = [var.count]
    }"""
    parsed = hcl2.loads(text, serialization_options=OPTIONS)
    validate_validation_coverage(
        {}, files(BOUNDS), SimpleNamespace(parsed={"test": parsed})
    )
