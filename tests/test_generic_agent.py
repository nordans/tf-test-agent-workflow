"""New-module discovery, multi-module isolation and generic HCL trust boundaries."""

import base64
import copy
import json
import os
import sys
from unittest.mock import Mock, patch

import pytest
from test_module_planner import FakeClient, tool_calls
from test_pr_workflow import (
    HCL,
    MODULE,
    RELTEST,
    ROOT,
    SCHEMA_HASH,
    SCHEMAS,
    TARGET,
    UPDATED,
    VARIABLES,
    analysis,
    changed_bundle,
    draft,
    execution,
    runner,
)
from test_pr_workflow import repository as repository

from test_agent.context import AgentError
from test_agent.pr_author import BudgetedClient, draft_tests
from test_agent.pr_context import GitRepository, PRIdentity
from test_agent.pr_results import (
    Draft,
    ModuleDraft,
    digest,
    suite_hash,
)
from test_agent.pr_workflow import main as workflow_main
from test_agent.pr_workflow import proposal_patch, render_comment, verify_tests
from test_agent.suite_runner import source_mutations, summarize
from test_agent.terraform_policy import (
    validate_output_coverage,
    validate_sources,
    validate_suite,
    validate_update,
)
from test_agent.verify_repository import verify_repository

AI_SOURCE = (ROOT / "tests/fixtures/ai_search_module/main.tf").read_text()
AI_HCL = (ROOT / "tests/fixtures/ai_search_valid.tftest.hcl").read_text()
ALL_SCHEMAS = copy.deepcopy(SCHEMAS)
ALL_SCHEMAS["registry.terraform.io/hashicorp/azurerm"]["resource_schemas"][
    "azurerm_search_service"
] = {
    "block": {
        "attributes": {
            "id": {"type": "string", "optional": True, "computed": True},
            "sku": {"type": "string", "required": True},
            "public_network_access_enabled": {"type": "bool", "optional": True},
            "tags": {"type": ["map", "string"], "optional": True},
        }
    },
}
AI_FILES = {"modules/ai-search/main.tf": AI_SOURCE}


def add_ai(repository, name="ai-search"):
    root, git, _ = repository
    path = root / f"modules/{name}/main.tf"
    path.parent.mkdir(parents=True)
    path.write_text(AI_SOURCE, encoding="utf-8")
    return path


def collect(repository):
    root, git, base = repository
    git("add", ".")
    git("commit", "-qm", "Synthetic module changes")
    return GitRepository(root).collect(
        PRIdentity(
            repository="owner/example",
            number=20,
            base_sha=base,
            head_sha=git("rev-parse", "HEAD"),
            origin="local",
        )
    )


def ai_plan(name="ai-search"):
    plan = analysis()
    plan["module"] = name
    plan["existing_test_files"] = []
    plan["plan"]["decision"] = "add_tests"
    plan["plan"]["summary"] = "Cover the new search module."
    plan["plan"]["test_cases"][0]["evidence"] = [
        {"file": f"modules/{name}/main.tf", "line": 1}
    ]
    return plan


def author_calls(content):
    return [
        tool_calls(("load_skill", {"name": "terraform-unit-testing"})),
        tool_calls(
            ("propose_test_files", {"files": [{"path": RELTEST, "content": content}]})
        ),
        tool_calls(("submit_proposal", {})),
    ]


def verified(tests):
    return {
        "passed": True,
        "terraform": execution(),
        "mutation": {"detected": True},
        "suite_sha256": suite_hash(tests),
    }


def test_new_module_is_discovered_without_registering_its_name(repository):
    add_ai(repository, name="previously-unseen-search")
    bundle = collect(repository)
    assert bundle.selected_modules == ["previously-unseen-search"] and bundle.eligible
    assert bundle.module_files("previously-unseen-search", "before") == {}
    assert (
        bundle.analysis_context(ROOT, "previously-unseen-search").module
        == "previously-unseen-search"
    )
    client = FakeClient(author_calls(AI_HCL))
    with patch(
        "test_agent.pr_author.run_planner",
        return_value=ai_plan("previously-unseen-search"),
    ):
        result = draft_tests(bundle, ROOT, client, ALL_SCHEMAS, SCHEMA_HASH)
    assert result.modules[0].status == "proposed"
    assert list(result.modules[0].edits) == [RELTEST]


def test_new_and_existing_modules_are_planned_verified_and_patched_separately(
    repository,
):
    root, _, _ = repository
    add_ai(repository)
    path = root / VARIABLES
    path.write_text(path.read_text().replace("default     = 30", "default     = 60"))
    bundle = collect(repository)
    client = FakeClient([*author_calls(AI_HCL), *author_calls(UPDATED)])
    with patch("test_agent.pr_author.run_planner", side_effect=[ai_plan(), analysis()]):
        item = draft_tests(bundle, ROOT, client, ALL_SCHEMAS, SCHEMA_HASH)
    assert [m.module for m in item.modules] == ["ai-search", MODULE]
    worker = Mock(schemas=ALL_SCHEMAS, schema_sha256=SCHEMA_HASH)
    worker.execute.side_effect = [
        (execution(), {RELTEST: HCL}),
        (execution(False, True), {RELTEST: HCL}),
    ]
    worker.verify.side_effect = lambda sources, tests: (verified(tests), tests)
    report = verify_tests(bundle, item, worker)
    assert report.status == "verified"
    assert report.modules[0].baseline is None
    assert report.modules[1].baseline["passed"]
    patch_text = proposal_patch(bundle, report)
    assert "modules/ai-search/tests/generated.tftest.hcl" in patch_text
    assert TARGET in patch_text
    assert all(
        len({p.split("/")[1] for p in call.args[0]}) == 1
        for call in worker.verify.call_args_list
    )


def test_unsupported_module_does_not_hide_supported_module(repository):
    root, _, _ = repository
    add_ai(repository)
    path = root / "modules/not-approved/main.tf"
    path.parent.mkdir()
    path.write_text(AI_SOURCE.replace("hashicorp/azurerm", "someone/azurerm"))
    bundle = collect(repository)
    assert bundle.eligible and bundle.eligible_modules == ["ai-search"]
    client = FakeClient(author_calls(AI_HCL))
    with patch("test_agent.pr_author.run_planner", return_value=ai_plan()) as planner:
        item = draft_tests(bundle, ROOT, client, ALL_SCHEMAS, SCHEMA_HASH)
    assert planner.call_count == 1
    assert [m.status for m in item.modules] == ["proposed", "needs_review"]
    worker = Mock(schemas=ALL_SCHEMAS, schema_sha256=SCHEMA_HASH)
    worker.verify.side_effect = lambda sources, tests: (verified(tests), tests)
    report = verify_tests(bundle, item, worker)
    assert report.status == "needs_review"
    assert "not-approved" not in proposal_patch(bundle, report)
    assert "| not-approved | needs_review |" in render_comment(bundle, item, report)


def test_missing_module_or_schema_digest_is_not_accepted(repository):
    add_ai(repository)
    bundle = collect(repository)
    item = Draft(
        identity=bundle.identity,
        bundle_sha256=digest(bundle),
        schema_sha256=SCHEMA_HASH,
        modules=[],
    )
    with pytest.raises(AgentError, match="every selected module"):
        verify_tests(bundle, item, None)
    item.modules = [
        ModuleDraft(module="ai-search", status="proposed", edits={RELTEST: AI_HCL})
    ]
    worker = Mock(schema_sha256="b" * 64)
    with pytest.raises(AgentError, match="schemas"):
        verify_tests(bundle, item, worker)
    worker.verify.assert_not_called()


def test_module_budget_is_checked_before_model_execution(repository):
    for index in range(9):
        add_ai(repository, name=f"search-{index}")
    with pytest.raises(AgentError, match="eight-module"):
        collect(repository)


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace("command = plan", "command = apply"),
        lambda t: t.replace('mock_provider "azurerm"', 'provider "azurerm"'),
        lambda t: t.replace(
            "length(azurerm_search_service.this.tags)", 'length(file("/etc/passwd"))'
        ),
        lambda t: t.replace("id = ", 'sku = "standard"\n      id = ', 1),
        lambda t: t.replace(
            'condition     = azurerm_search_service.this.sku == "basic"',
            "condition     = true",
        ),
    ],
)
def test_generic_policy_rejects_execution_real_providers_and_configurable_mocks(edit):
    with pytest.raises(AgentError):
        validate_suite(AI_FILES, {RELTEST: edit(AI_HCL)}, ALL_SCHEMAS)


def test_all_test_files_are_retained_and_assertions_cannot_be_removed():
    extra = "tests/extra.tftest.hcl"
    previous = {
        RELTEST: AI_HCL,
        extra: AI_HCL.replace('run "defaults"', 'run "more_defaults"'),
    }
    with pytest.raises(AgentError, match="FILES_MUST_BE_PRESERVED"):
        validate_update(AI_FILES, AI_FILES, previous, {RELTEST: AI_HCL}, ALL_SCHEMAS)
    weakened = AI_HCL.replace(
        'azurerm_search_service.this.sku == "basic"',
        'azurerm_search_service.this.sku != ""',
    )
    with pytest.raises(AgentError, match="ASSERTIONS_MUST_BE_PRESERVED"):
        validate_update(
            AI_FILES, AI_FILES, {RELTEST: AI_HCL}, {RELTEST: weakened}, ALL_SCHEMAS
        )


def test_source_boolean_expectations_cannot_be_silently_weakened():
    changed = {
        "modules/ai-search/main.tf": AI_SOURCE.replace(
            "public_network_access_enabled = false",
            "public_network_access_enabled = true",
        )
    }
    hcl = AI_HCL.replace(
        "public_network_access_enabled == false",
        "public_network_access_enabled == true",
    )
    with pytest.raises(AgentError, match="ASSERTIONS_MUST_BE_PRESERVED"):
        validate_update(
            AI_FILES, changed, {RELTEST: AI_HCL}, {RELTEST: hcl}, ALL_SCHEMAS
        )


def test_new_required_input_can_be_added_without_changing_old_inputs():
    changed = {
        "modules/ai-search/main.tf": AI_SOURCE + '\nvariable "team" { type = string }\n'
    }
    hcl = AI_HCL.replace(
        'name = "search-test"', 'name = "search-test"\n  team = "platform"'
    )
    validate_update(AI_FILES, changed, {RELTEST: AI_HCL}, {RELTEST: hcl}, ALL_SCHEMAS)
    with pytest.raises(AgentError, match="INPUTS_MUST_BE_PRESERVED"):
        validate_update(
            AI_FILES,
            changed,
            {RELTEST: AI_HCL},
            {RELTEST: hcl.replace('name = "search-test"', 'name = "different"')},
            ALL_SCHEMAS,
        )


def test_provisioners_and_filesystem_source_functions_are_rejected():
    body = AI_SOURCE.replace(
        'location                      = "westeurope"', 'location = file("/secret")'
    )
    with pytest.raises(AgentError):
        validate_sources({"modules/ai-search/main.tf": body})
    body = AI_SOURCE.replace(
        "tags                          = var.tags",
        'tags = var.tags\n  provisioner "local-exec" { command = "do-not-run" }',
    )
    with pytest.raises(AgentError, match="EXECUTION"):
        validate_sources({"modules/ai-search/main.tf": body})


def test_source_mutations_are_bounded_and_do_not_modify_supplied_sources():
    original = dict(AI_FILES)
    suite = validate_suite(AI_FILES, {RELTEST: AI_HCL}, ALL_SCHEMAS)
    mutations = list(source_mutations(AI_FILES, suite))
    assert 1 <= len(mutations) <= 3 and AI_FILES == original
    assert any(
        'sku                           = "basic"' in files["modules/ai-search/main.tf"]
        for _, _, files in mutations
    )


def test_request_budget_stops_without_an_extra_model_call():
    client = FakeClient([])
    budget = BudgetedClient(client)
    budget.calls = 64
    with pytest.raises(AgentError, match="budget"):
        budget.generate("", [], [])
    assert not client.requests


def test_verifier_rejects_a_path_not_offered_to_the_author(repository):
    bundle = changed_bundle(repository)
    item = draft(bundle)
    item.modules[0].edits = {"tests/unoffered.tftest.hcl": UPDATED}
    worker = runner()
    result = verify_tests(bundle, item, worker)
    assert result.status == "needs_review"
    assert "not offered" in result.modules[0].reason
    worker.verify.assert_not_called()


def test_computed_id_exception_cannot_mask_a_configured_id():
    changed = {
        "modules/ai-search/main.tf": AI_SOURCE.replace(
            "sku                           = var.sku",
            'sku = var.sku\n  id = "/configured/id"',
        )
    }
    with pytest.raises(AgentError, match="CONFIGURED_ID"):
        validate_suite(changed, {RELTEST: AI_HCL}, ALL_SCHEMAS)


def test_expected_failures_require_an_implemented_validation():
    hcl = AI_HCL.replace(
        "command = plan", "command = plan\n  expect_failures = [var.name]", 1
    )
    with pytest.raises(AgentError, match="REFERENCE_VALIDATION"):
        validate_suite(AI_FILES, {RELTEST: hcl}, ALL_SCHEMAS)


def test_qualified_runs_prevent_duplicate_names_from_hiding_a_missing_file():
    tests = {RELTEST: AI_HCL, "tests/other.tftest.hcl": AI_HCL}
    suite = validate_suite(AI_FILES, tests, ALL_SCHEMAS)
    events = [{"runner_stage": "test"}]
    for path, text in tests.items():
        events.append(
            {
                "formatted_test": path,
                "content_base64": base64.b64encode(text.encode()).decode(),
            }
        )
    for _ in range(2):
        for _, name in suite.runs[:3]:
            events.append(
                {
                    "type": "test_run",
                    "test_run": {
                        "path": RELTEST,
                        "run": name,
                        "progress": "complete",
                        "status": "pass",
                    },
                }
            )
    events.append(
        {
            "type": "test_summary",
            "test_summary": {
                "status": "pass",
                "passed": 6,
                "failed": 0,
                "errored": 0,
                "skipped": 0,
            },
        }
    )
    raw = "\n".join(json.dumps(e) for e in events).encode()
    result, _ = summarize(raw, 0, suite, AI_FILES, ALL_SCHEMAS)
    assert not result["passed"]


@pytest.mark.parametrize("changed,expected", [(False, True), (True, False)])
def test_ci_records_missing_tests_and_blocks_changed_untested_modules(
    tmp_path, changed, expected
):
    module = tmp_path / "modules/ai-search"
    module.mkdir(parents=True)
    (module / "main.tf").write_text(AI_SOURCE)
    worker = Mock()
    report = verify_repository(tmp_path, worker, ["ai-search"] if changed else [])
    assert report["passed"] is expected
    assert report["untested_modules"] == ["ai-search"]
    worker.verify.assert_not_called()


def test_ci_requires_a_source_mutation_for_changed_modules(tmp_path):
    module = tmp_path / "modules/ai-search"
    (module / "tests").mkdir(parents=True)
    (module / "main.tf").write_text(AI_SOURCE)
    (module / RELTEST).write_text(AI_HCL)
    worker = Mock()
    worker.verify.return_value = (
        {"terraform": {"passed": True}, "mutation": {"detected": False}},
        {RELTEST: AI_HCL},
    )
    report = verify_repository(tmp_path, worker, ["ai-search"])
    assert not report["passed"] and report["modules"][0]["status"] == "failed"


def test_formatter_only_proposal_is_verified_as_no_change(repository):
    bundle = changed_bundle(repository, applied=True)
    item = draft(bundle, "\n" + UPDATED)
    worker = runner(applied=True)
    report = verify_tests(bundle, item, worker)
    assert report.status == "no_change_verified"
    assert not proposal_patch(bundle, report)


def test_output_checks_require_computed_values_to_be_mocked_during_plan():
    hcl = AI_HCL.replace("  override_during = plan\n", "")
    with pytest.raises(AgentError, match="REQUIRE_PLAN_MOCKS"):
        validate_suite(AI_FILES, {RELTEST: hcl}, ALL_SCHEMAS)


def test_repair_cannot_drop_new_module_output_coverage():
    hcl = AI_HCL.split('run "output_id"')[0]
    suite = validate_suite(AI_FILES, {RELTEST: hcl}, ALL_SCHEMAS)
    with pytest.raises(AgentError, match="OUTPUT_ASSERTIONS_MISSING"):
        validate_output_coverage({}, AI_FILES, suite, None)


def test_prepare_requests_scan_for_ineligible_source_snapshots(repository, tmp_path):
    path = add_ai(repository)
    path.write_text(AI_SOURCE.replace("hashicorp/azurerm", "someone/azurerm"))
    bundle = collect(repository)
    assert not bundle.eligible and bundle.after_files
    output = tmp_path / "stage-output"
    with (
        patch("test_agent.pr_workflow.prepare", return_value=bundle),
        patch.object(
            sys, "argv", ["agent", "prepare", "--output", str(tmp_path / "artifacts")]
        ),
        patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}),
    ):
        assert workflow_main() == 0
    assert "eligible=false" in output.read_text()
    assert "scan_required=true" in output.read_text()


def test_stack_wiring_requires_review_without_blocking_leaf_module_tests(repository):
    root, _, _ = repository
    add_ai(repository)
    path = root / "stacks/demo/main.tf"
    path.parent.mkdir(parents=True)
    path.write_text('module "search" { source = "../../modules/ai-search" }')
    bundle = collect(repository)
    assert bundle.eligible and bundle.review_reasons and not bundle.blocking_reasons
    client = FakeClient(author_calls(AI_HCL))
    with patch("test_agent.pr_author.run_planner", return_value=ai_plan()):
        item = draft_tests(bundle, ROOT, client, ALL_SCHEMAS, SCHEMA_HASH)
    worker = Mock(schemas=ALL_SCHEMAS, schema_sha256=SCHEMA_HASH)
    worker.verify.side_effect = lambda sources, tests: (verified(tests), tests)
    report = verify_tests(bundle, item, worker)
    assert report.status == "needs_review"
    assert report.modules[0].status == "verified"
    assert "modules/ai-search/" in proposal_patch(bundle, report)
    assert "Stack impact analysis" in render_comment(bundle, item, report)
