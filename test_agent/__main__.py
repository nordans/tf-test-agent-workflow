"""Run with: python -m test_agent log-analytics-workspace."""

import argparse
import json
import os
import sys
from pathlib import Path

from test_agent.context import AgentError, AnalysisContext
from test_agent.gemini import GeminiClient
from test_agent.planner import run_planner


def artifact_directory(root: Path, module: str) -> Path:
    output_root = root / ".agent-output"
    output = output_root / module
    if any(path.is_symlink() or path.is_junction() for path in (output_root, output)):
        raise AgentError("Linked artifact directories are not allowed.")
    for filename in ("generated.tftest.hcl", "proposal.patch", "report.json"):
        path = output / filename
        if path.exists() or path.is_symlink() or path.is_junction():
            raise AgentError(
                "Artifacts already exist; move the previous run before generating again."
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read one Terraform module and propose a test plan."
    )
    parser.add_argument(
        "module", help="Module directory name, for example log-analytics-workspace"
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Propose and sandbox-verify tests for the supported module",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        context = AnalysisContext(
            source_root=root, trusted_root=root, module=args.module
        )
        if args.generate:
            from test_agent.author import run_author
            from test_agent.sandbox import DockerRunner

            output = artifact_directory(root, args.module)
            # Fail before spending model quota if the runtime is unavailable.
            runner = DockerRunner()
            client = GeminiClient(os.environ.get("GEMINI_API_KEY", ""))
            report, proposal = run_author(context, client, runner)
            output.mkdir(parents=True, exist_ok=True)
            artifacts = {
                "generated.tftest.hcl": proposal,
                "proposal.patch": report["diff"],
                "report.json": json.dumps(report, indent=2, ensure_ascii=True) + "\n",
            }
            for filename, text in artifacts.items():
                with (output / filename).open(
                    "x", encoding="utf-8", newline="\n"
                ) as file:
                    file.write(text)
        else:
            client = GeminiClient(os.environ.get("GEMINI_API_KEY", ""))
            report = run_planner(context, client)
    except AgentError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    # JSON escapes newlines in model text, preventing it becoming workflow commands.
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if not args.generate or report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
