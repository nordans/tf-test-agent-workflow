"""Revision-bound multi-module PR workflow, with independent execution and publication."""

import argparse
import difflib
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from test_agent.behavior import validate_boolean_coverage
from test_agent.context import AgentError
from test_agent.gemini import GeminiClient
from test_agent.github import MARKER, GitHub
from test_agent.pr_author import draft_tests
from test_agent.pr_context import GitRepository, PRBundle, PRIdentity
from test_agent.pr_results import (
    Draft,
    ModuleDraft,
    ModuleVerification,
    Verification,
    digest,
    read_json,
    report_status,
    same_suites,
    same_test_content,
    write_json,
)
from test_agent.sandbox import docker_environment
from test_agent.suite_runner import SuiteRunner
from test_agent.terraform_policy import (
    test_files,
    validate_output_coverage,
    validate_update,
)

ROOT = Path(__file__).resolve().parents[1]


def write_snapshot(directory, bundle):
    for revision, files in (
        ("before", bundle.before_files),
        ("after", bundle.after_files),
    ):
        for name, content in files.items():
            target = directory / revision / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as file:
                file.write(content)


def scan_local_source(bundle, executable):
    with tempfile.TemporaryDirectory(prefix="tf-agent-source-scan-") as directory:
        write_snapshot(Path(directory), bundle)
        try:
            result = subprocess.run(
                [
                    executable,
                    "dir",
                    directory,
                    "--redact=100",
                    "--no-banner",
                    "--no-color",
                    "--ignore-gitleaks-allow",
                    "--timeout",
                    "120",
                ],
                env=docker_environment(),
                capture_output=True,
                timeout=130,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise AgentError(
                "Install Betterleaks 1.9.0 or pass --secret-scanner with its executable path."
            ) from None
        if result.returncode:
            raise AgentError(
                "Source secret scan did not pass; no source was sent to Gemini."
            )


def verify_one(bundle, item, runner):
    module = item.module
    report = ModuleVerification(
        module=module, status="needs_review", reason=item.reason
    )
    if item.status not in ("proposed", "no_change"):
        report.status = "skipped" if item.status == "skipped" else "needs_review"
        return report
    before, after = (
        bundle.module_files(module, "before"),
        bundle.module_files(module, "after"),
    )
    previous, existing = test_files(before, module), test_files(after, module)
    existing_formatted = existing
    candidate = {**existing, **item.edits}
    try:
        if set(item.edits) - set(existing) - {"tests/generated.tftest.hcl"}:
            raise AgentError("Proposal contains a test path not offered by the caller.")
        suite = validate_update(before, after, previous, candidate, runner.schemas)
        validate_update(
            before, after, existing, candidate, runner.schemas, previous_sources=after
        )
        validate_output_coverage(
            before, after, suite, item.analysis.plan if item.analysis else None
        )
        validate_boolean_coverage(before, after, suite)
        if previous:
            report.baseline, _ = runner.execute(before, previous)
            if not report.baseline["passed"]:
                report.reason = "Existing tests already fail on the before source."
                return report
        if existing:
            try:
                report.existing, existing_formatted = runner.execute(after, existing)
            except AgentError as error:
                report.existing = {"passed": False, "policy_error": str(error)}
        report.candidate, report.tests = runner.verify(after, candidate)
        if not report.candidate["passed"]:
            report.reason = (
                "The candidate suite passed, but the bounded mutation sample did not produce a test assertion failure. Sensitivity remains inconclusive; review the controls."
                if report.candidate["terraform"]["passed"]
                else "The candidate test suite did not pass. Review the native diagnostics."
            )
            return report
        if same_suites(report.tests, existing_formatted):
            if not (report.existing or {}).get("passed"):
                report.reason = (
                    "Identical tests produced inconsistent execution results."
                )
                return report
            report.status = "no_change_verified"
        else:
            report.status = "verified"
        report.reason = ""
    except AgentError as error:
        report.status, report.reason = "needs_review", str(error)
    return report


def check_bindings(bundle, draft, report=None):
    PRBundle.model_validate(bundle.model_dump())
    Draft.model_validate(draft.model_dump())
    if draft.identity != bundle.identity or draft.bundle_sha256 != digest(bundle):
        raise AgentError("Draft identity or source digest does not match.")
    if [m.module for m in draft.modules] != bundle.selected_modules:
        raise AgentError("Draft does not account for every selected module.")
    if report is not None:
        Verification.model_validate(report.model_dump())
        if (
            report.identity != bundle.identity
            or report.bundle_sha256 != digest(bundle)
            or report.draft_sha256 != digest(draft)
        ):
            raise AgentError("Report identity or stage digests do not match.")
        if [m.module for m in report.modules] != bundle.selected_modules:
            raise AgentError("Report does not account for every selected module.")
        if report.review_reasons != bundle.review_reasons:
            raise AgentError("Report omitted an unsupported impact warning.")


def verify_tests(bundle, draft, runner):
    check_bindings(bundle, draft)
    if any(m.status in ("proposed", "no_change") for m in draft.modules):
        if runner is None or draft.schema_sha256 != runner.schema_sha256:
            raise AgentError(
                "Approved provider schemas do not match the proposal stage."
            )
    reports = []
    for item in draft.modules:
        if bundle.blocking_reasons or item.module in bundle.module_reasons:
            reports.append(
                ModuleVerification(
                    module=item.module,
                    status="needs_review",
                    reason="; ".join(bundle.blocking_reasons)
                    or bundle.module_reasons[item.module],
                )
            )
        else:
            reports.append(verify_one(bundle, item, runner))
    return Verification(
        identity=bundle.identity,
        bundle_sha256=digest(bundle),
        draft_sha256=digest(draft),
        modules=reports,
        status="needs_review"
        if bundle.review_reasons and report_status(reports) != "failed"
        else report_status(reports),
        reason=draft.reason,
        review_reasons=bundle.review_reasons,
    )


def proposal_patch(bundle, report):
    if report.status == "failed":
        return ""
    chunks = []
    for module in report.modules:
        if module.status != "verified":
            continue
        current = test_files(bundle.module_files(module.module, "after"), module.module)
        for path, text in sorted(module.tests.items()):
            if same_test_content(current.get(path), text):
                continue
            target = f"modules/{module.module}/{path}"
            chunks.extend(
                difflib.unified_diff(
                    current.get(path, "").splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=f"a/{target}" if path in current else "/dev/null",
                    tofile=f"b/{target}",
                )
            )
    return "".join(chunks)


def unverified_patch(bundle, draft, report):
    """Keep failed proposals reviewable without granting verification status."""
    chunks = []
    for item, result in zip(draft.modules, report.modules, strict=True):
        if item.status != "proposed" or (
            report.status != "failed"
            and result.status in ("verified", "no_change_verified")
        ):
            continue
        current = test_files(bundle.module_files(item.module, "after"), item.module)
        if set(item.edits) - set(current) - {"tests/generated.tftest.hcl"}:
            continue
        for path, content in sorted(item.edits.items()):
            target = f"modules/{item.module}/{path}"
            for line in difflib.unified_diff(
                current.get(path, "").splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{target}" if path in current else "/dev/null",
                tofile=f"b/{target}",
            ):
                chunks.append(
                    line
                    if line.endswith("\n")
                    else line + "\n\\ No newline at end of file\n"
                )
    return "".join(chunks)


def literal(text):
    value = html.escape(str(text), quote=False).replace("@", "[at]")
    return re.sub(r"([\\\x60*_\[\]()#!|~])", r"\\\1", value)


def render_comment(bundle, draft, report, run_id=None):
    check_bindings(bundle, draft, report)
    identity = bundle.identity
    lines = [
        MARKER,
        "### Terraform test agent",
        f"Result: **{report.status}**",
        f"Source: head {identity.head_sha}, base {identity.base_sha}, merge base {bundle.merge_base}.",
        "| Module | Verified result |\n|---|---|\n"
        + "\n".join(f"| {m.module} | {m.status} |" for m in report.modules)
        if report.modules
        else "No selected Terraform modules.",
    ]
    if report.reason:
        lines.append(literal(report.reason))
    lines.extend(
        "Review required: " + literal(reason) for reason in report.review_reasons
    )
    for item, result in zip(draft.modules, report.modules, strict=True):
        lines.append(f"#### {item.module}")
        if item.analysis:
            plan = item.analysis.plan
            lines.extend(
                [
                    "Analysis skill: terraform-change-analysis.",
                    f"Planner decision: **{plan.decision}**",
                    literal(plan.summary),
                ]
            )
            if "load_skill" in item.tool_calls:
                lines.append("Authoring skill: terraform-unit-testing.")
            if plan.test_cases:
                lines.append(
                    "Planned cases: "
                    + ", ".join(literal(case.name[:80]) for case in plan.test_cases[:6])
                    + (
                        "; more in the draft artifact."
                        if len(plan.test_cases) > 6
                        else "."
                    )
                )
            for finding in plan.findings[:3]:
                locations = ", ".join(
                    f"{e.revision}:{e.file}:{e.line}" for e in finding.evidence[:2]
                )
                lines.append(
                    f"- {finding.severity}: {literal(finding.description[:300])} ({literal(locations)})"
                )
            if len(plan.findings) > 3:
                lines.append("Additional findings are retained in the draft artifact.")
        if result.reason:
            lines.append(literal(result.reason[:1000]))
        for label, execution in (
            ("Before-source tests", result.baseline),
            ("Existing head tests", result.existing),
        ):
            if execution is not None:
                lines.append(
                    f"{label}: {'passed' if execution.get('passed') else 'did not pass'}."
                )
        if result.candidate is not None:
            candidate = result.candidate
            lines.extend(
                [
                    f"Candidate tests: {'passed' if candidate['terraform']['passed'] else 'did not pass'}.",
                    f"Source mutation detected: {candidate['mutation'].get('detected', False)}.",
                ]
            )
            if not candidate["terraform"]["passed"]:
                if item.analysis and item.analysis.plan.decision == "no_change":
                    lines.append(
                        "The planner's no_change decision did not pass independent verification."
                    )
                for diagnostic in candidate["terraform"].get("diagnostics", [])[:3]:
                    if isinstance(diagnostic, dict):
                        summary = str(diagnostic.get("summary", ""))[:200]
                        detail = str(diagnostic.get("detail", ""))[:600]
                        lines.append(
                            "Verifier diagnostic: " + literal(f"{summary}: {detail}")
                        )
            elif not candidate["mutation"].get("detected"):
                for control in candidate["mutation"].get("attempts", [])[:3]:
                    execution = control.get("terraform", {})
                    outcome = (
                        "tests still passed"
                        if execution.get("passed")
                        else "setup/runtime error; not assertion detection"
                    )
                    lines.append(
                        "Mutation control: "
                        + literal(str(control.get("description", ""))[:250])
                        + " - "
                        + outcome
                        + "."
                    )
        if result.status == "no_change_verified":
            lines.append(
                "No test changes are needed. Existing tests passed independent execution and a source-mutation control."
            )
    if proposal_patch(bundle, report):
        lines.append(
            "The pr-result artifact contains a patch for verified modules. Review and apply it to this same PR. Infrastructure was not modified."
        )
    if unverified_patch(bundle, draft, report):
        lines.append(
            "Unverified test proposal: pr-result/unverified.patch contains the author's draft for modules that did not pass verification. It is separate from proposal.patch. Inspect and correct it before applying; it is not an approved test patch."
        )
    if run_id is not None:
        if not re.fullmatch(r"[0-9]+", run_id):
            raise AgentError("Invalid workflow run ID.")
        lines.append(
            f"[Run and artifacts](https://github.com/{identity.repository}/actions/runs/{run_id})"
        )
    lines.append(
        "Mocked plans do not prove live Azure behavior. A detected mutation is not a coverage metric. Findings and proposed changes require human review."
    )
    return "\n\n".join(lines)


def event_target():
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise AgentError("Privileged PR commands run only from main.")
    event = read_json(Path(os.environ["GITHUB_EVENT_PATH"]))
    name = os.environ.get("GITHUB_EVENT_NAME")
    if name == "pull_request_target":
        number = event["pull_request"]["number"]
    elif name == "workflow_dispatch":
        raw = str(event["inputs"]["pr_number"])
        if not re.fullmatch(r"[1-9][0-9]{0,8}", raw):
            raise AgentError("Invalid dispatched PR number.")
        number = int(raw)
    else:
        raise AgentError("Unsupported privileged workflow event.")
    return event, number


def prepare(root):
    event, number = event_target()
    github = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
    identity, reasons = github.pull_request(number)
    if "pull_request" in event:
        original = event["pull_request"]
        if (
            original["head"]["sha"] != identity.head_sha
            or original["base"]["sha"] != identity.base_sha
        ):
            reasons.append("The PR changed after this event was created.")
            identity = PRIdentity(
                repository=identity.repository,
                number=number,
                base_sha=original["base"]["sha"],
                head_sha=original["head"]["sha"],
            )
    if reasons:
        return PRBundle(
            identity=identity,
            merge_base=identity.base_sha,
            changes=[],
            selected_modules=[],
            reasons=reasons,
            before_files={},
            after_files={},
        )
    return GitRepository(root).collect(identity)


def stage_output(name, value):
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as file:
            file.write(f"{name}={'true' if value else 'false'}\n")


def save_result(directory, bundle, draft, report):
    check_bindings(bundle, draft, report)
    write_json(directory / "report.json", report)
    for name, text in (
        ("proposal.patch", proposal_patch(bundle, report)),
        ("unverified.patch", unverified_patch(bundle, draft, report)),
        ("comment.md", render_comment(bundle, draft, report)),
    ):
        with (directory / name).open("x", encoding="utf-8", newline="\n") as file:
            file.write(text)


def missing_report(bundle, draft):
    modules = [
        ModuleVerification(
            module=item.module,
            status="failed"
            if item.status in ("proposed", "no_change")
            else item.status,
            reason="The verification job produced no result."
            if item.status in ("proposed", "no_change")
            else item.reason,
        )
        for item in draft.modules
    ]
    return Verification(
        identity=bundle.identity,
        bundle_sha256=digest(bundle),
        draft_sha256=digest(draft),
        status="needs_review"
        if bundle.review_reasons and report_status(modules) != "failed"
        else report_status(modules),
        modules=modules,
        reason=draft.reason,
        review_reasons=bundle.review_reasons,
    )


def missing_draft(bundle):
    reason = "; ".join(bundle.blocking_reasons) or (
        "The author job produced no result."
        if bundle.selected_modules
        else "No Terraform module changes."
    )
    return Draft(
        identity=bundle.identity,
        bundle_sha256=digest(bundle),
        reason=reason if not bundle.module_reasons else "",
        modules=[
            ModuleDraft(
                module=module,
                status="needs_review",
                reason=bundle.module_reasons.get(module) or reason,
            )
            for module in bundle.selected_modules
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=("prepare", "draft", "verify", "publish", "local")
    )
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--input", type=Path, default=Path("incoming"))
    parser.add_argument("--output", type=Path, default=Path("out"))
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--repository", default="local/example")
    parser.add_argument("--number", type=int, default=1)
    parser.add_argument("--secret-scanner", default="betterleaks")
    args = parser.parse_args()
    runner = None
    try:
        if args.stage == "prepare":
            bundle = prepare(args.repo)
            write_json(args.output / "context.json", bundle)
            if bundle.before_files or bundle.after_files:
                write_snapshot(args.output / "scan", bundle)
            stage_output("eligible", bundle.eligible)
            stage_output(
                "scan_required", bool(bundle.before_files or bundle.after_files)
            )
            return 0
        if args.stage == "local":
            identity = PRIdentity(
                repository=args.repository,
                number=args.number,
                base_sha=args.base,
                head_sha=args.head,
                origin="local",
            )
            bundle = GitRepository(args.repo).collect(identity)
            if bundle.before_files or bundle.after_files:
                scan_local_source(bundle, args.secret_scanner)
            write_json(args.output / "context.json", bundle)
        else:
            context_path = args.input / "context.json"
            if args.stage == "publish" and not context_path.exists():
                _, number = event_target()
                identity, _ = GitHub(
                    os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
                ).pull_request(number)
                bundle = PRBundle(
                    identity=identity,
                    merge_base=identity.base_sha,
                    changes=[],
                    selected_modules=[],
                    reasons=[
                        "Preparation failed; no sources or proposals were verified."
                    ],
                    before_files={},
                    after_files={},
                )
            else:
                bundle = PRBundle.model_validate(read_json(context_path))
        if args.stage in ("local", "draft"):
            client = None
            if bundle.eligible:
                if args.stage == "draft":
                    current, reasons = GitHub(
                        bundle.identity.repository, os.environ["GITHUB_TOKEN"]
                    ).pull_request(bundle.identity.number)
                    if current != bundle.identity or reasons:
                        raise AgentError(
                            "PR is no longer eligible for a live model call."
                        )
                runner = SuiteRunner()
                client = GeminiClient(os.environ.get("GEMINI_API_KEY", ""))
            draft = draft_tests(
                bundle,
                ROOT,
                client,
                runner.schemas if runner else None,
                runner.schema_sha256 if runner else None,
            )
            write_json(args.output / "draft.json", draft)
            stage_output("verify", draft.status in ("proposed", "no_change"))
            if args.stage == "draft":
                return 0
        else:
            path = args.input / "draft.json"
            draft = (
                Draft.model_validate(read_json(path))
                if path.exists()
                else missing_draft(bundle)
            )
        if args.stage in ("local", "verify"):
            if GitRepository(args.repo).collect(bundle.identity) != bundle:
                raise AgentError(
                    "Source artifact does not match the recorded Git revisions."
                )
            if runner is None and draft.status in ("proposed", "no_change"):
                runner = SuiteRunner()
            report = verify_tests(bundle, draft, runner)
            save_result(args.output, bundle, draft, report)
            print(f"PR agent result: {report.status}.")
            return (
                0
                if report.status in ("verified", "no_change_verified", "skipped")
                else 1
            )
        path = args.input / "report.json"
        report = (
            Verification.model_validate(read_json(path))
            if path.exists()
            else missing_report(bundle, draft)
        )
        if os.environ.get("VERIFIER_RESULT") == "failure" and report.status in (
            "verified",
            "no_change_verified",
        ):
            report.status, report.reason = (
                "failed",
                "The verification job failed after producing its report.",
            )
        if (
            os.environ.get("PREPARE_RESULT") != "success"
            or os.environ.get("AUTHOR_RESULT") == "failure"
        ):
            report.status, report.reason = (
                "failed",
                "Preparation or model authoring failed; see the workflow diagnostics.",
            )
        event, number = event_target()
        if "pull_request" in event and (
            bundle.identity.head_sha != event["pull_request"]["head"]["sha"]
            or bundle.identity.base_sha != event["pull_request"]["base"]["sha"]
        ):
            print("Stale event: report not published.")
            return 0
        if (
            bundle.identity.repository != os.environ["GITHUB_REPOSITORY"]
            or bundle.identity.number != number
        ):
            raise AgentError("Artifact identity does not match this workflow's PR.")
        body = render_comment(bundle, draft, report, os.environ["GITHUB_RUN_ID"])
        published = GitHub(
            bundle.identity.repository, os.environ["GITHUB_TOKEN"]
        ).publish(bundle.identity, body)
        print(
            "PR report published."
            if published
            else "Stale or closed PR: report not published."
        )
        return 0
    except (
        AgentError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        UnicodeError,
        OSError,
        RecursionError,
    ) as error:
        print(
            f"FAIL: {str(error) if isinstance(error, AgentError) else 'Invalid stage input or configuration.'}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
