"""Run every checked-in module suite; require tests and mutation detection for changed modules."""

import json
import os
import sys
from pathlib import Path

from test_agent.context import AgentError, AnalysisContext
from test_agent.pr_context import GitRepository, PRIdentity
from test_agent.suite_runner import SuiteRunner
from test_agent.terraform_policy import test_files

ROOT = Path(__file__).resolve().parents[1]


def verify_repository(root, runner, changed=()):
    reports, missing = [], []
    directory = root / "modules"
    if directory.is_symlink() or directory.is_junction():
        raise AgentError("Linked module directories are not allowed.")
    modules = sorted(p for p in directory.iterdir() if p.is_dir())
    if len(modules) > 32:
        raise AgentError("Repository verification exceeds its 32-module budget.")
    for module in modules:
        try:
            context = AnalysisContext(root, ROOT, module.name)
            tests = test_files(context.files, module.name)
            if not tests:
                missing.append(module.name)
                reports.append(
                    {
                        "module": module.name,
                        "status": "failed"
                        if module.name in changed
                        else "missing_tests",
                        "reason": "No checked-in tests.",
                        "changed": module.name in changed,
                    }
                )
                continue
            verification, _ = runner.verify(context.files, tests)
            passed = verification["terraform"]["passed"] and (
                module.name not in changed or verification["mutation"]["detected"]
            )
            reports.append(
                {
                    "module": module.name,
                    "status": "passed" if passed else "failed",
                    "changed": module.name in changed,
                    "verification": verification,
                }
            )
        except AgentError as error:
            reports.append(
                {"module": module.name, "status": "failed", "reason": str(error)}
            )
    return {
        "passed": bool(reports) and not any(r["status"] == "failed" for r in reports),
        "modules": reports,
        "untested_modules": missing,
        "mutation_policy": "Changed modules require source-mutation detection. Existing gaps in unchanged suites are reported, not treated as coverage.",
    }


def main():
    try:
        changed = []
        base, head = os.environ.get("TF_TEST_BASE"), os.environ.get("TF_TEST_HEAD")
        if base or head:
            identity = PRIdentity(
                repository=os.environ.get("GITHUB_REPOSITORY", "local/example"),
                number=1,
                base_sha=base,
                head_sha=head,
                origin="local",
            )
            changed = GitRepository(ROOT).collect(identity).selected_modules
        report = verify_repository(ROOT, SuiteRunner(), changed)
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    except (AgentError, ValueError) as error:
        print(
            f"FAIL: {str(error) if isinstance(error, AgentError) else 'Invalid revision metadata.'}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
