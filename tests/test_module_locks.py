"""Module lock metadata cannot select packages or obscure an eligibility failure."""

import json
from unittest.mock import Mock

import pytest
from test_pr_workflow import MODULE, ROOT, changed_bundle
from test_pr_workflow import repository as repository

from test_agent.context import MAX_FILE_BYTES, AgentError
from test_agent.pr_author import draft_tests
from test_agent.pr_context import GitRepository, PRIdentity
from test_agent.pr_workflow import missing_draft, missing_report, render_comment


def seed_lock(repository):
    root, git, _ = repository
    path = root / f"modules/{MODULE}/.terraform.lock.hcl"
    path.write_text("# Existing module lock metadata.\n")
    git("add", ".")
    git("commit", "-qm", "Existing module lock")
    return (root, git, git("rev-parse", "HEAD")), path


def test_unchanged_module_lock_is_not_model_or_execution_input(repository):
    repository, _ = seed_lock(repository)
    bundle = changed_bundle(repository)
    assert bundle.eligible and not bundle.module_reasons
    assert not any(".terraform.lock.hcl" in p for p in bundle.before_files)
    assert not any(".terraform.lock.hcl" in p for p in bundle.after_files)
    assert ".terraform.lock.hcl" not in json.dumps(
        bundle.analysis_context(ROOT).module_data()
    )


@pytest.mark.parametrize("operation", ["add", "modify", "delete"])
def test_changed_module_lock_requires_review_and_reports_the_real_reason(
    repository, operation
):
    if operation == "add":
        path = repository[0] / f"modules/{MODULE}/.terraform.lock.hcl"
    else:
        repository, path = seed_lock(repository)
    if operation == "delete":
        path.unlink()
    else:
        path.write_text("# PR-selected provider lock metadata.\n")
    bundle = changed_bundle(repository)
    assert not bundle.eligible and bundle.after_files
    assert "lockfile changes require human review" in bundle.module_reasons[MODULE]
    client = Mock()
    item = draft_tests(bundle, ROOT, client)
    client.generate.assert_not_called()
    assert item.modules[0].reason == bundle.module_reasons[MODULE]

    missing = missing_draft(bundle)
    report = missing_report(bundle, missing)
    comment = render_comment(bundle, missing, report)
    assert report.status == "needs_review"
    assert "lockfile changes require human review" in comment
    assert "author job produced no result" not in comment


def test_lock_change_does_not_block_an_independent_module(repository):
    root, _, _ = repository
    lock = root / f"modules/{MODULE}/.terraform.lock.hcl"
    lock.write_text("# New lock metadata.\n")
    path = root / "modules/ai-search/main.tf"
    path.parent.mkdir()
    path.write_text((ROOT / "tests/fixtures/ai_search_module/main.tf").read_text())
    bundle = changed_bundle(repository)
    assert bundle.eligible
    assert bundle.eligible_modules == ["ai-search"]
    assert MODULE in bundle.module_reasons


def test_renaming_a_lock_to_an_allowed_source_path_still_requires_review(repository):
    repository, _ = seed_lock(repository)
    _, git, _ = repository
    git(
        "mv",
        f"modules/{MODULE}/.terraform.lock.hcl",
        f"modules/{MODULE}/old-lock.tf",
    )
    bundle = changed_bundle(repository)
    assert not bundle.eligible
    assert "lockfile changes require human review" in bundle.module_reasons[MODULE]


@pytest.mark.parametrize("mode", ["120000", "100755"])
def test_linked_or_executable_lock_is_rejected_even_when_excluded(repository, mode):
    root, git, _ = repository
    oid = git("hash-object", "-w", "--stdin", input=b"outside")
    path = f"modules/{MODULE}/.terraform.lock.hcl"
    git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
    git("commit", "-qm", "Unsafe lock entry")
    with pytest.raises(AgentError, match="Linked, executable"):
        GitRepository(root).snapshot(git("rev-parse", "HEAD"), MODULE)


def test_excluded_lock_still_has_a_size_bound(repository):
    root, git, _ = repository
    (root / f"modules/{MODULE}/.terraform.lock.hcl").write_text(
        "x" * (MAX_FILE_BYTES + 1)
    )
    git("add", ".")
    git("commit", "-qm", "Oversized lock entry")
    with pytest.raises(AgentError, match="input limits"):
        GitRepository(root).snapshot(git("rev-parse", "HEAD"), MODULE)


def test_missing_author_result_retains_the_failure_reason_for_eligible_input(
    repository,
):
    bundle = changed_bundle(repository)
    item = missing_draft(bundle)
    assert "author job produced no result" in item.reason
    assert item.modules[0].reason == item.reason


def test_skipped_author_reports_a_protected_policy_change(repository):
    root, _, _ = repository
    path = root / "agent/instructions.md"
    path.parent.mkdir()
    path.write_text("Changed policy")
    bundle = changed_bundle(repository)
    item = missing_draft(bundle)
    comment = render_comment(bundle, item, missing_report(bundle, item))
    assert "Agent policy, runtime or workflow changes require human review" in comment
    assert "author job produced no result" not in comment


def test_documentation_only_pr_does_not_report_a_missing_author_result(repository):
    root, git, base = repository
    (root / "README.md").write_text("Documentation update")
    git("add", ".")
    git("commit", "-qm", "Documentation only")
    bundle = GitRepository(root).collect(
        PRIdentity(
            repository="owner/example",
            number=1,
            base_sha=base,
            head_sha=git("rev-parse", "HEAD"),
            origin="local",
        )
    )
    item = missing_draft(bundle)
    report = missing_report(bundle, item)
    assert report.status == "skipped"
    assert "No Terraform module changes" in render_comment(bundle, item, report)
