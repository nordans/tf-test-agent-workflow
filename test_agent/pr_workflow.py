"""PR stage commands and a local end-to-end entry point."""

import argparse
import difflib
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from test_agent.context import AgentError
from test_agent.gemini import GeminiClient
from test_agent.github import MARKER, GitHub
from test_agent.hcl_policy import validate_proposal, validate_update
from test_agent.pr_author import draft_tests
from test_agent.pr_context import TARGET, GitRepository, PRBundle, PRIdentity
from test_agent.pr_results import (
    Draft,
    Verification,
    digest,
    read_json,
    same_test_content,
    write_json,
)
from test_agent.sandbox import DockerRunner, docker_environment

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


def verify_tests(bundle, draft, runner):
    if draft.identity != bundle.identity or draft.bundle_sha256 != digest(bundle):
        raise AgentError("Draft identity or source snapshot does not match.")
    report = Verification(
        identity=bundle.identity,
        bundle_sha256=digest(bundle),
        draft_sha256=digest(draft),
        status="skipped",
        reason=draft.reason,
    )
    if not bundle.eligible or draft.status not in ("proposed", "no_change"):
        report.status = "needs_review" if draft.status == "needs_review" else "skipped"
        return report
    try:
        previous = bundle.before_files.get(TARGET)
        if previous is not None:
            report.baseline, _ = runner.execute(
                bundle.before_files, validate_proposal(previous)
            )
            if not report.baseline["passed"]:
                report.status, report.reason = (
                    "needs_review",
                    "Existing tests already fail on the before source.",
                )
                return report
        existing = bundle.after_files.get(TARGET)
        if existing is not None:
            try:
                report.existing, _ = runner.execute(
                    bundle.after_files, validate_proposal(existing)
                )
            except AgentError as error:
                report.existing = {"passed": False, "policy_error": str(error)}
        candidate = draft.hcl if draft.status == "proposed" else existing
        if candidate is None:
            raise AgentError("No test file is available for verification.")
        proposal = validate_update(
            previous, candidate, bundle.before_files, bundle.after_files
        )
        report.candidate, report.hcl = runner.verify(bundle.after_files, proposal)
        if not report.candidate["passed"]:
            report.status, report.reason = (
                "needs_review",
                "Proposed tests or their retention negative control did not pass.",
            )
        else:
            report.status = (
                "no_change_verified"
                if same_test_content(candidate, existing)
                else "verified"
            )
    except AgentError as error:
        report.status, report.reason = "failed", str(error)
    return report


def proposal_patch(bundle, report):
    if report.status != "verified" or report.hcl is None:
        return ""
    previous = bundle.after_files.get(TARGET)
    return "".join(
        difflib.unified_diff(
            (previous or "").splitlines(keepends=True),
            report.hcl.splitlines(keepends=True),
            fromfile=f"a/{TARGET}" if previous is not None else "/dev/null",
            tofile=f"b/{TARGET}",
        )
    )


def literal(text):
    value = html.escape(str(text), quote=False).replace("@", "[at]")
    return re.sub(r"([\\\x60*_\[\]()#!|~])", r"\\\1", value)


def render_comment(bundle, draft, report, run_id=None):
    Verification.model_validate(report.model_dump())
    if report.identity != bundle.identity or draft.identity != bundle.identity:
        raise AgentError("Report targets a different PR.")
    if report.bundle_sha256 != digest(bundle) or report.draft_sha256 != digest(draft):
        raise AgentError("Stage artifact digests do not match.")
    identity = bundle.identity
    lines = [
        MARKER,
        "### Terraform test agent",
        f"Result: **{report.status}**",
        f"Source: head {identity.head_sha}, base {identity.base_sha}, merge base {bundle.merge_base}.",
        "Modules: "
        + (", ".join(literal(m) for m in bundle.selected_modules) or "none"),
    ]
    if report.reason:
        lines.append(literal(report.reason))
    if draft.analysis:
        plan = draft.analysis.plan
        lines.append("Analysis skill: terraform-change-analysis.")
        if "load_skill" in draft.tool_calls:
            lines.append("Authoring skill: terraform-unit-testing.")
        lines.extend([f"Planner decision: **{plan.decision}**", literal(plan.summary)])
        for finding in plan.findings:
            locations = ", ".join(
                f"{e.revision}:{e.file}:{e.line}" for e in finding.evidence
            )
            lines.append(
                f"- {finding.severity}: {literal(finding.description)} ({literal(locations)})"
            )
    for name, result in (
        ("Before-source tests", report.baseline),
        ("Existing head tests", report.existing),
    ):
        if result is not None:
            lines.append(
                f"{name}: {'passed' if result.get('passed') else 'did not pass'}."
            )
    if report.candidate is not None:
        lines.append(
            f"Candidate tests: {'passed' if report.candidate['terraform']['passed'] else 'did not pass'}."
        )
        lines.append(
            f"Retention mutation detected: {report.candidate['mutation'].get('detected', False)}."
        )
    if report.status == "no_change_verified":
        lines.append(
            "No test changes are needed. Existing PR tests passed verification and the retention negative control."
        )
    if proposal_patch(bundle, report):
        lines.append(
            "A verified test patch is available in the pr-result artifact. Review and apply it to this same PR; infrastructure was not modified."
        )
    if run_id is not None:
        if not re.fullmatch(r"[0-9]+", run_id):
            raise AgentError("Invalid workflow run ID.")
        lines.append(
            f"[Run and artifacts](https://github.com/{identity.repository}/actions/runs/{run_id})"
        )
    lines.append(
        "Mocked plans do not prove live Azure behavior. Findings and proposed changes require human review."
    )
    return "\n\n".join(lines)[:20000]


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
    Verification.model_validate(report.model_dump())
    write_json(directory / "report.json", report)
    for name, text in (
        ("proposal.patch", proposal_patch(bundle, report)),
        ("comment.md", render_comment(bundle, draft, report)),
    ):
        with (directory / name).open("x", encoding="utf-8", newline="\n") as file:
            file.write(text)


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
    local_runner = None
    try:
        if args.stage == "prepare":
            bundle = prepare(args.repo)
            write_json(args.output / "context.json", bundle)
            if bundle.eligible:
                write_snapshot(args.output / "scan", bundle)
            stage_output("eligible", bundle.eligible)
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
            if bundle.eligible:
                local_runner = DockerRunner()
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
                    reasons=["Preparation failed; no proposal was verified."],
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
                client = GeminiClient(os.environ.get("GEMINI_API_KEY", ""))
            draft = draft_tests(bundle, ROOT, client)
            write_json(args.output / "draft.json", draft)
            stage_output("verify", draft.status in ("proposed", "no_change"))
            if args.stage == "draft":
                return 0
        else:
            path = args.input / "draft.json"
            draft = (
                Draft.model_validate(read_json(path))
                if path.exists()
                else Draft(
                    identity=bundle.identity,
                    bundle_sha256=digest(bundle),
                    status="needs_review" if bundle.eligible else "skipped",
                    reason="The author job produced no result."
                    if bundle.eligible
                    else "; ".join(bundle.reasons) or "No supported Terraform changes.",
                )
            )
        if args.stage in ("local", "verify"):
            if (
                bundle.eligible
                and GitRepository(args.repo).collect(bundle.identity) != bundle
            ):
                raise AgentError(
                    "Source artifact does not match the recorded Git revisions."
                )
            report = verify_tests(
                bundle,
                draft,
                local_runner or DockerRunner()
                if bundle.eligible and draft.status in ("proposed", "no_change")
                else None,
            )
            save_result(args.output, bundle, draft, report)
            print(f"PR agent result: {report.status}.")
            return (
                0
                if report.status in ("verified", "no_change_verified", "skipped")
                else 1
            )
        report_path = args.input / "report.json"
        report = (
            Verification.model_validate(read_json(report_path))
            if report_path.exists()
            else Verification(
                identity=bundle.identity,
                bundle_sha256=digest(bundle),
                draft_sha256=digest(draft),
                status="failed"
                if bundle.eligible and draft.status in ("proposed", "no_change")
                else draft.status
                if draft.status in ("skipped", "needs_review")
                else "failed",
                reason="The verification job produced no result."
                if bundle.eligible and draft.status in ("proposed", "no_change")
                else draft.reason,
            )
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
                "Preparation or model authoring failed. See the workflow run for diagnostics.",
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
    ) as error:
        message = (
            str(error)
            if isinstance(error, AgentError)
            else "Invalid stage input or configuration."
        )
        print(f"FAIL: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
