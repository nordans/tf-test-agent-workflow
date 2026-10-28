"""PR selection, guarded updates, stage boundaries and safe publication."""

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from test_module_planner import FakeClient, tool_calls

from test_agent.context import AgentError, sha256_text
from test_agent.github import MARKER, GitHub
from test_agent.hcl_policy import validate_update
from test_agent.pr_author import draft_tests
from test_agent.pr_context import (
    TARGET,
    Change,
    GitRepository,
    PRBundle,
    PRIdentity,
    classify,
)
from test_agent.pr_results import Analysis, Draft, digest
from test_agent.pr_workflow import (
    proposal_patch,
    render_comment,
    scan_local_source,
    verify_tests,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = "log-analytics-workspace"
VARIABLES = f"modules/{MODULE}/variables.tf"
FIXTURE = ROOT / "tests/fixtures/log_analytics_module"
# Controller tests own their baseline; infrastructure PRs may change real defaults.
ORIGINAL = {
    f"modules/{MODULE}/{path.name}": path.read_text(encoding="utf-8")
    for path in FIXTURE.glob("*.tf")
}
ORIGINAL[TARGET] = (ROOT / "tests/fixtures/log_analytics_valid.tftest.hcl").read_text(
    encoding="utf-8"
)
HCL = ORIGINAL[TARGET]
UPDATED = HCL.replace("retention_in_days == 30", "retention_in_days == 60")


@pytest.fixture
def repository(tmp_path):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def git(*args, input=None):
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Agent tests",
                "-c",
                "user.email=agent@example.invalid",
                "-C",
                str(tmp_path),
                *args,
            ],
            input=input,
            capture_output=True,
            env=env,
            check=True,
        )
        return result.stdout.decode().strip()

    git("init", "-q")
    for name, content in ORIGINAL.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "Base module")
    base = git("rev-parse", "HEAD")
    return tmp_path, git, base


def changed_bundle(repository, *, applied=False):
    root, git, base = repository
    path = root / VARIABLES
    path.write_text(path.read_text().replace("default     = 30", "default     = 60"))
    if applied:
        (root / TARGET).write_text(UPDATED)
    git("add", ".")
    git("commit", "-qm", "Change default retention")
    identity = PRIdentity(
        repository="owner/example",
        number=12,
        base_sha=base,
        head_sha=git("rev-parse", "HEAD"),
        origin="local",
    )
    return GitRepository(root).collect(identity)


def analysis():
    return {
        "mode": "analysis_only",
        "module": MODULE,
        "model": "fixture",
        "tests_executed": False,
        "tool_calls": [],
        "existing_test_files": [TARGET],
        "provenance": {},
        "usage": {},
        "plan": {
            "decision": "update_tests",
            "summary": "Update the default retention expectation.",
            "test_cases": [
                {
                    "name": "default",
                    "category": "default",
                    "basis": "implemented_behavior",
                    "purpose": "Follow the changed default.",
                    "expected": "Retention equals 60.",
                    "evidence": [{"file": VARIABLES, "line": 19}],
                }
            ],
            "findings": [],
            "limitations": ["No live Azure verification."],
        },
    }


def draft(bundle, hcl=UPDATED):
    return Draft(
        identity=bundle.identity,
        bundle_sha256=digest(bundle),
        status="proposed",
        hcl=hcl,
    )


def execution(passed=True, assertion=False):
    return {
        "passed": passed,
        "assertion_failure": assertion,
        "runs": [{"name": "default", "status": "pass" if passed else "fail"}],
    }


def runner():
    instance = Mock()
    instance.execute.side_effect = [(execution(), HCL), (execution(False, True), HCL)]
    instance.verify.return_value = (
        {
            "passed": True,
            "terraform": execution(),
            "mutation": {"detected": True},
            "proposal_sha256": sha256_text(UPDATED),
        },
        UPDATED,
    )
    return instance


def test_real_git_diff_collects_before_after_and_preserves_source_checkout(repository):
    bundle = changed_bundle(repository)
    assert bundle.eligible
    assert bundle.selected_modules == [MODULE]
    assert "default     = 30" in bundle.before_files[VARIABLES]
    assert "default     = 60" in bundle.after_files[VARIABLES]
    assert repository[1]("rev-parse", "HEAD") == bundle.identity.head_sha
    context = bundle.analysis_context(ROOT)
    assert context.module_data()["pull_request"]["head_sha"] == bundle.identity.head_sha
    assert "before/" in context.module_data()["pull_request"]["diff"]


def test_docs_only_never_calls_the_model(repository):
    root, git, base = repository
    (root / "README.md").write_text("Documentation")
    git("add", ".")
    git("commit", "-qm", "Docs")
    identity = PRIdentity(
        repository="owner/example",
        number=1,
        base_sha=base,
        head_sha=git("rev-parse", "HEAD"),
    )
    bundle = GitRepository(root).collect(identity)
    client = Mock()
    assert draft_tests(bundle, ROOT, client).status == "skipped"
    client.generate.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "test_agent/context.py",
        "skills/example/SKILL.md",
        ".github/workflows/new.yml",
        "runtime/Dockerfile",
        "stacks/data-platform/main.tf",
        "modules/network/main.tf",
        f"modules/{MODULE}/nested/main.tf",
        f"modules/{MODULE}/main.tf.json",
    ],
)
def test_unhandled_or_policy_changes_require_review(path):
    _, reasons = classify([Change(status="M", path=path)])
    assert reasons


def test_cross_module_rename_selects_both_modules():
    selected, reasons = classify(
        [
            Change(
                status="R",
                path="modules/network/main.tf",
                previous_path=f"modules/{MODULE}/main.tf",
            )
        ]
    )
    assert selected == [MODULE, "network"]
    assert reasons


@pytest.mark.parametrize(
    "path",
    [
        "../main.tf",
        "modules/../main.tf",
        "/tmp/main.tf",
        "modules/x\\main.tf",
        "bad\nname.tf",
    ],
)
def test_unsafe_paths_are_rejected(path):
    with pytest.raises(ValueError):
        Change(status="M", path=path)


def test_symlink_blob_is_rejected_without_following_it(repository):
    root, git, base = repository
    oid = git("hash-object", "-w", "--stdin", input=b"/etc/passwd")
    git("update-index", "--cacheinfo", f"120000,{oid},modules/{MODULE}/main.tf")
    git("commit", "-qm", "Linked input")
    identity = PRIdentity(
        repository="owner/example",
        number=1,
        base_sha=base,
        head_sha=git("rev-parse", "HEAD"),
    )
    with patch.object(
        GitRepository, "snapshot", side_effect=AssertionError("must not read")
    ):
        bundle = GitRepository(root).collect(identity)
    assert not bundle.eligible
    assert any("file-type" in reason for reason in bundle.reasons)


def test_snapshot_tampering_cannot_change_selection(repository):
    bundle = changed_bundle(repository)
    data = bundle.model_dump()
    data["selected_modules"] = []
    with pytest.raises(ValueError):
        PRBundle.model_validate(data)
    data = bundle.model_dump()
    data["after_files"]["../secrets"] = "untrusted"
    with pytest.raises(ValueError):
        PRBundle.model_validate(data)


def test_only_source_backed_default_equality_may_change(repository):
    bundle = changed_bundle(repository)
    validate_update(HCL, UPDATED, bundle.before_files, bundle.after_files)
    for content in (
        UPDATED.replace('== "PerGB2018"', '== "Other"'),
        UPDATED.replace(
            "length(azurerm_log_analytics_workspace.this.tags) == 0", "true"
        ),
        UPDATED.replace("retention_in_days = 90", "retention_in_days = 60"),
        HCL.replace("retention_in_days == 30", "retention_in_days == 90"),
    ):
        with pytest.raises(AgentError):
            validate_update(HCL, content, bundle.before_files, bundle.after_files)
    with pytest.raises(AgentError):
        validate_update(HCL, UPDATED, bundle.before_files, bundle.before_files)


def test_model_stages_use_skill_and_do_not_expose_execution_tools(repository):
    bundle = changed_bundle(repository)
    client = FakeClient(
        [
            tool_calls(("load_skill", {"name": "terraform-unit-testing"})),
            tool_calls(("propose_test_file", {"content": UPDATED})),
            tool_calls(("submit_proposal", {})),
        ]
    )
    with patch("test_agent.pr_author.run_planner", return_value=analysis()):
        result = draft_tests(bundle, ROOT, client)
    assert result.status == "proposed"
    assert result.tool_calls == ["load_skill", "propose_test_file", "submit_proposal"]
    assert all(
        "verify_module" not in [t["name"] for t in req["tools"]]
        for req in client.requests
    )


def test_proposed_requirement_does_not_auto_rewrite_infrastructure(repository):
    bundle = changed_bundle(repository)
    plan = analysis()
    plan["plan"]["test_cases"][0]["basis"] = "proposed_requirement"
    plan["plan"]["findings"] = [
        {
            "severity": "warning",
            "description": "Security behavior weakened.",
            "evidence": [{"file": VARIABLES, "line": 19}],
        }
    ]
    client = Mock(usage={})
    with patch("test_agent.pr_author.run_planner", return_value=plan):
        result = draft_tests(bundle, ROOT, client)
    assert result.status == "needs_review"
    client.generate.assert_not_called()


def test_baseline_existing_and_candidate_are_distinct_and_patch_updates_file(
    repository,
):
    bundle = changed_bundle(repository)
    worker = runner()
    result = verify_tests(bundle, draft(bundle), worker)
    assert result.status == "verified"
    assert result.baseline["passed"] and not result.existing["passed"]
    assert result.candidate["passed"]
    patch_text = proposal_patch(bundle, result)
    assert f"--- a/{TARGET}" in patch_text and "== 60" in patch_text


def test_broken_baseline_stops_candidate_execution(repository):
    bundle = changed_bundle(repository)
    worker = runner()
    worker.execute.side_effect = [(execution(False, True), HCL)]
    assert verify_tests(bundle, draft(bundle), worker).status == "needs_review"
    worker.verify.assert_not_called()


def test_no_change_still_requires_real_verification(repository):
    bundle = changed_bundle(repository, applied=True)
    item = draft(bundle)
    item.status, item.hcl = "no_change", None
    worker = runner()
    result = verify_tests(bundle, item, worker)
    assert result.status == "no_change_verified"
    worker.verify.assert_called_once()
    assert not proposal_patch(bundle, result)


def test_reports_reject_wrong_identity_and_digest(repository):
    bundle = changed_bundle(repository)
    item = draft(bundle)
    result = verify_tests(bundle, item, runner())
    result.draft_sha256 = "0" * 64
    with pytest.raises(AgentError):
        render_comment(bundle, item, result)
    item.identity = item.identity.model_copy(update={"number": 999})
    with pytest.raises(AgentError):
        verify_tests(bundle, item, runner())


def test_rendered_model_content_cannot_create_mentions_or_active_links(repository):
    bundle = changed_bundle(repository)
    item = draft(bundle)
    record = analysis()
    record["plan"]["summary"] = "@someone [click](https://evil.invalid) <script>"
    item.analysis = Analysis.model_validate(record)
    result = verify_tests(bundle, item, runner())
    comment = render_comment(bundle, item, result, "123")
    assert "@someone" not in comment and "<script>" not in comment
    assert r"\[click\]" in comment
    assert "actions/runs/123" in comment


def test_github_errors_do_not_expose_tokens():
    connection = Mock()
    connection.request.side_effect = OSError("private-token")
    with patch(
        "test_agent.github.http.client.HTTPSConnection", return_value=connection
    ):
        with pytest.raises(AgentError) as error:
            GitHub("owner/example", "private-token").pull_request(1)
    assert "private-token" not in str(error.value)


def test_stale_or_closed_pr_is_never_published(repository):
    identity = changed_bundle(repository).identity.model_copy(
        update={"origin": "github"}
    )
    client = GitHub(identity.repository, "fixture-token")
    client.pull_request = Mock(
        return_value=(identity.model_copy(update={"head_sha": "f" * 40}), [])
    )
    client.request = Mock()
    assert not client.publish(identity, "body")
    client.request.assert_not_called()


def test_publisher_updates_only_its_own_marked_comment(repository):
    identity = changed_bundle(repository).identity.model_copy(
        update={"origin": "github"}
    )
    client = GitHub(identity.repository, "fixture-token")
    client.pull_request = Mock(return_value=(identity, []))
    attacker = {"id": 1, "body": MARKER, "user": {"login": "attacker", "type": "User"}}
    owned = {
        "id": 2,
        "body": MARKER,
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    client.request = Mock(side_effect=[[attacker, owned], {}])
    assert client.publish(identity, MARKER + "\nReport")
    assert client.request.call_args.args[:2] == ("PATCH", "issues/comments/2")


def test_merge_base_excludes_unrelated_target_branch_commits(repository):
    root, git, base = repository
    bundle = changed_bundle(repository)
    git("checkout", "-qb", "target-advanced", base)
    (root / "README.md").write_text("Unrelated target branch documentation")
    git("add", ".")
    git("commit", "-qm", "Target advanced")
    identity = bundle.identity.model_copy(update={"base_sha": git("rev-parse", "HEAD")})
    result = GitRepository(root).collect(identity)
    assert result.merge_base == base
    assert [change.path for change in result.changes] == [VARIABLES]


@pytest.mark.parametrize(
    "name,args",
    [
        ("verify_module", {}),
        ("propose_test_file", {"content": UPDATED, "path": "main.tf"}),
        ("submit_proposal", {"passed": True}),
    ],
)
def test_model_cannot_request_execution_arbitrary_paths_or_results(
    repository, name, args
):
    bundle = changed_bundle(repository)
    responses = [tool_calls(("load_skill", {"name": "terraform-unit-testing"}))]
    if name == "submit_proposal":
        responses.append(tool_calls(("propose_test_file", {"content": UPDATED})))
    responses.append(tool_calls((name, args)))
    with patch("test_agent.pr_author.run_planner", return_value=analysis()):
        with pytest.raises(AgentError):
            draft_tests(bundle, ROOT, FakeClient(responses))


def test_failed_mutation_cannot_produce_a_verified_patch(repository):
    bundle = changed_bundle(repository)
    worker = runner()
    worker.verify.return_value = (
        {"passed": False, "terraform": execution(), "mutation": {"detected": False}},
        UPDATED,
    )
    report = verify_tests(bundle, draft(bundle), worker)
    assert report.status == "needs_review"
    assert not proposal_patch(bundle, report)


def test_head_move_during_comment_lookup_prevents_writing(repository):
    identity = changed_bundle(repository).identity.model_copy(
        update={"origin": "github"}
    )
    client = GitHub(identity.repository, "fixture-token")
    client.pull_request = Mock(
        side_effect=[
            (identity, []),
            (identity.model_copy(update={"head_sha": "e" * 40}), []),
        ]
    )
    client.request = Mock(return_value=[])
    assert not client.publish(identity, "report")
    assert client.request.call_count == 1


def test_closed_and_local_only_results_are_never_published(repository):
    identity = changed_bundle(repository).identity
    client = GitHub(identity.repository, "fixture-token")
    client.pull_request = Mock(return_value=(identity, []))
    client.request = Mock()
    assert not client.publish(identity, "report")
    client.pull_request.return_value = (
        identity.model_copy(update={"origin": "github"}),
        ["The pull request is closed."],
    )
    assert not client.publish(client.pull_request.return_value[0], "report")
    client.request.assert_not_called()


def test_verified_label_cannot_override_missing_or_negative_execution_evidence(
    repository,
):
    bundle = changed_bundle(repository)
    item = draft(bundle)
    report = verify_tests(bundle, item, runner())
    for candidate in (
        None,
        {
            "passed": True,
            "terraform": {"passed": False},
            "mutation": {"detected": True},
        },
        {
            "passed": True,
            "terraform": {"passed": True},
            "mutation": {"detected": False},
        },
    ):
        altered = report.model_copy(update={"candidate": candidate})
        with pytest.raises(ValueError):
            render_comment(bundle, item, altered)


def test_source_scan_failure_stops_with_no_credentials_forwarded(repository):
    bundle = changed_bundle(repository)
    with patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "private-model-key", "GITHUB_TOKEN": "private-github-token"},
    ):
        with patch(
            "test_agent.pr_workflow.subprocess.run", return_value=Mock(returncode=1)
        ) as command:
            with pytest.raises(AgentError, match="no source was sent"):
                scan_local_source(bundle, "betterleaks")
    assert "GEMINI_API_KEY" not in command.call_args.kwargs["env"]
    assert "GITHUB_TOKEN" not in command.call_args.kwargs["env"]
    assert "--redact=100" in command.call_args.args[0]
