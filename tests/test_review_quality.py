"""Regressions observed in the real AKS experiment, without a live model."""

import re
from unittest.mock import patch

import pytest
from test_generic_agent import (
    AI_FILES,
    AI_HCL,
    AI_SOURCE,
    ALL_SCHEMAS,
    add_ai,
    ai_plan,
    author_calls,
    collect,
)
from test_module_planner import FakeClient
from test_pr_workflow import (
    RELTEST,
    ROOT,
    SCHEMA_HASH,
    TARGET,
    UPDATED,
    changed_bundle,
    draft,
    execution,
    runner,
)
from test_pr_workflow import repository as repository

from test_agent.behavior import (
    boolean_properties,
    required_booleans,
    resource_attributes,
    validate_boolean_coverage,
)
from test_agent.context import AgentError
from test_agent.pr_author import draft_tests
from test_agent.pr_results import Draft, ModuleDraft, digest
from test_agent.pr_workflow import (
    proposal_patch,
    render_comment,
    save_result,
    unverified_patch,
    verify_tests,
)
from test_agent.suite_runner import source_mutations
from test_agent.terraform_policy import validate_suite


def without_boolean(hcl=AI_HCL):
    return re.sub(
        r'  assert \{\s*condition\s*= azurerm_search_service.this.public_network_access_enabled == false\s*error_message\s*= "[^"]*"\s*\}',
        "",
        hcl,
    )


def nested_files():
    return {
        "modules/ai-search/main.tf": AI_SOURCE.replace(
            "  tags                          = var.tags",
            """  pool {
    capacity = var.capacity
    public_enabled = false
  }
  tags = var.tags""",
        )
        + '\nvariable "capacity" {\n type = number\n default = 2\n}\n'
    }


def test_nested_paths_are_preserved_and_change_source_locations_exactly():
    files = nested_files()
    attributes = list(resource_attributes(files))
    nested = next(a for a in attributes if a.path == "pool.capacity")
    assert nested.reference == "azurerm_search_service.this.pool.capacity"
    assert files[nested.file][nested.start : nested.end] == "var.capacity"
    assert (
        "azurerm_search_service.this.pool.public_enabled",
        False,
    ) in boolean_properties(files)


def test_nested_asserted_override_is_in_the_three_control_sample():
    files = nested_files()
    hcl = AI_HCL.replace(
        '    sku  = "standard"', '    capacity = 3\n    sku  = "standard"'
    ).replace(
        'condition     = azurerm_search_service.this.sku == "standard"',
        "condition     = azurerm_search_service.this.pool[0].capacity == 3",
    )
    original = dict(files)
    suite = validate_suite(files, {RELTEST: hcl}, ALL_SCHEMAS)
    controls = list(source_mutations(files, suite))
    assert len(controls) <= 3 and files == original
    nested = next(c for c in controls if "Freeze pool.capacity" in c[1])
    assert "capacity = 2" in nested[2]["modules/ai-search/main.tf"]


def test_validation_rejections_do_not_displace_a_real_nested_override():
    files = nested_files()
    path = "modules/ai-search/main.tf"
    files[path] = (
        files[path]
        .replace(
            'variable "name" {\n  type = string\n}',
            'variable "name" {\n type = string\n validation {\n condition = length(trimspace(var.name)) > 0\n error_message = "Name required"\n }\n}',
        )
        .replace("sku                           = var.sku", 'sku = "basic"')
    )
    hcl = (
        AI_HCL.replace(
            '    sku  = "standard"', '    capacity = 3\n    sku  = "standard"'
        ).replace(
            'condition     = azurerm_search_service.this.sku == "standard"',
            "condition     = azurerm_search_service.this.pool[0].capacity == 3",
        )
        + """
run "invalid_name" {
  command = plan
  variables { name = "   " }
  expect_failures = [var.name]
}
"""
    )
    suite = validate_suite(files, {RELTEST: hcl}, ALL_SCHEMAS)
    controls = list(source_mutations(files, suite))
    assert "Freeze pool.capacity" in controls[0][1]


def test_boolean_inventory_requires_only_new_or_changed_properties():
    after = nested_files()
    required = required_booleans(AI_FILES, after)
    assert required == [
        {
            "attribute": "azurerm_search_service.this.pool.public_enabled",
            "expected": False,
        }
    ]
    assert required_booleans(after, after) == []
    changed = {
        p: t.replace("public_enabled = false", "public_enabled = true")
        for p, t in after.items()
    }
    assert required_booleans(after, changed)[0]["expected"] is True


@pytest.mark.parametrize(
    "replacement",
    [
        "true",
        "false || true",
        "azurerm_search_service.this.public_network_access_enabled",
    ],
)
def test_flag_mentions_and_tautologies_do_not_satisfy_boolean_guard(replacement):
    hcl = AI_HCL.replace(
        "azurerm_search_service.this.public_network_access_enabled == false",
        "azurerm_search_service.this.public_network_access_enabled == " + replacement,
    )
    suite = validate_suite(AI_FILES, {RELTEST: hcl}, ALL_SCHEMAS)
    with pytest.raises(AgentError, match="CONFIGURED_BOOLEAN_ASSERTIONS_MISSING"):
        validate_boolean_coverage({}, AI_FILES, suite)


@pytest.mark.parametrize(
    "reference",
    [
        "azurerm_search_service.this.pool[0].public_enabled",
        "one(azurerm_search_service.this.pool).public_enabled",
    ],
)
def test_nested_boolean_guard_accepts_list_and_singleton_set_access(reference):
    hcl = AI_HCL.replace(
        'condition     = azurerm_search_service.this.sku == "basic"',
        f"condition     = {reference} == false",
    )
    files = nested_files()
    validate_boolean_coverage(
        {}, files, validate_suite(files, {RELTEST: hcl}, ALL_SCHEMAS)
    )


def test_author_can_correct_missing_boolean_without_dropping_other_checks(repository):
    add_ai(repository)
    bundle = collect(repository)
    bad, good = author_calls(without_boolean()), author_calls(AI_HCL)
    client = FakeClient([bad[0], bad[1], good[1], good[2]])
    with patch("test_agent.pr_author.run_planner", return_value=ai_plan()):
        result = draft_tests(bundle, ROOT, client, ALL_SCHEMAS, SCHEMA_HASH)
    assert result.modules[0].status == "proposed"
    error = client.requests[2]["contents"][-1]["parts"][0]["functionResponse"][
        "response"
    ]
    assert "CONFIGURED_BOOLEAN_ASSERTIONS_MISSING" in error["error"]
    assert "output.id" in result.modules[0].edits[RELTEST]


def test_verifier_rechecks_missing_booleans_before_any_execution(repository):
    add_ai(repository)
    bundle = collect(repository)
    item = Draft(
        identity=bundle.identity,
        bundle_sha256=digest(bundle),
        schema_sha256=SCHEMA_HASH,
        modules=[
            ModuleDraft(
                module="ai-search",
                status="proposed",
                edits={RELTEST: without_boolean()},
            )
        ],
    )
    worker = runner()
    worker.schemas = ALL_SCHEMAS
    report = verify_tests(bundle, item, worker)
    assert (
        report.status == "needs_review"
        and "CONFIGURED_BOOLEAN_ASSERTIONS_MISSING" in report.modules[0].reason
    )
    worker.verify.assert_not_called()
    worker.execute.assert_not_called()
    assert not proposal_patch(bundle, report)
    assert "modules/ai-search/tests/generated.tftest.hcl" in unverified_patch(
        bundle, item, report
    )


def test_failed_candidate_has_separate_reviewable_patch_and_native_diagnostics(
    repository, tmp_path
):
    bundle, worker = changed_bundle(repository), runner()
    worker.verify.return_value = (
        {
            "passed": False,
            "terraform": {
                **execution(False, True),
                "diagnostics": [
                    {
                        "summary": "Test assertion failed",
                        "detail": "Expected a private endpoint",
                    }
                ],
            },
            "mutation": {"detected": False},
        },
        {RELTEST: UPDATED},
    )
    item = draft(bundle)
    report = verify_tests(bundle, item, worker)
    save_result(tmp_path, bundle, item, report)
    assert not (tmp_path / "proposal.patch").read_text()
    assert "retention_in_days == 60" in (tmp_path / "unverified.patch").read_text()
    comment = (tmp_path / "comment.md").read_text()
    assert (
        "Unverified test proposal" in comment
        and "Expected a private endpoint" in comment
    )
    assert report.status == "needs_review"


def test_unverified_patch_preserves_no_final_newline_and_only_touches_tests(repository):
    bundle, worker = changed_bundle(repository), runner()
    item = draft(bundle, UPDATED.rstrip("\n"))
    worker.verify.return_value = (
        {"passed": False, "terraform": execution(), "mutation": {"detected": False}},
        item.modules[0].edits,
    )
    report = verify_tests(bundle, item, worker)
    contents = unverified_patch(bundle, item, report)
    root, git, _ = repository
    (root / TARGET).write_bytes(bundle.after_files[TARGET].encode())
    git("apply", "--check", input=contents.encode())
    git("apply", input=contents.encode())
    assert (root / TARGET).read_bytes() == UPDATED.rstrip("\n").encode()
    assert "No newline at end of file" in contents


def test_verified_tests_do_not_also_appear_in_unverified_patch(repository):
    bundle, worker = changed_bundle(repository), runner()
    item = draft(bundle)
    report = verify_tests(bundle, item, worker)
    assert proposal_patch(bundle, report) and not unverified_patch(bundle, item, report)


def test_failed_job_never_labels_its_previous_result_as_a_verified_patch(repository):
    bundle, worker = changed_bundle(repository), runner()
    item = draft(bundle)
    report = verify_tests(bundle, item, worker)
    report.status = "failed"
    assert not proposal_patch(bundle, report)
    assert "retention_in_days == 60" in unverified_patch(bundle, item, report)


def test_dynamic_content_wrapper_and_literal_content_block_are_distinct():
    files = {
        "module.tf": """resource "azurerm_example" "this" {
          dynamic "rule" {
            for_each = var.rules
            content { secure = true }
          }
          content { secure = false }
        }"""
    }
    assert boolean_properties(files) == [
        ("azurerm_example.this.content.secure", False),
        ("azurerm_example.this.rule.secure", True),
    ]


def test_mutation_runtime_error_is_explained_without_accepting_it(repository):
    bundle, worker = changed_bundle(repository), runner()
    item = draft(bundle)
    worker.verify.return_value = (
        {
            "passed": False,
            "terraform": execution(),
            "mutation": {
                "detected": False,
                "attempts": [
                    {
                        "description": "Freeze tags <script>@reviewer",
                        "terraform": {"passed": False},
                    }
                ],
            },
        },
        {RELTEST: UPDATED},
    )
    report = verify_tests(bundle, item, worker)
    comment = render_comment(bundle, item, report)
    assert "Sensitivity remains inconclusive" in comment
    assert "setup/runtime error; not assertion detection" in comment
    assert "<script>" not in comment and "@reviewer" not in comment
