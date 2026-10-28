"""Run with: python -m test_agent log-analytics-workspace."""

import argparse
import json
import os
import sys
from pathlib import Path

from test_agent.context import AgentError, AnalysisContext
from test_agent.gemini import GeminiClient
from test_agent.planner import run_planner


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read one Terraform module and propose a test plan."
    )
    parser.add_argument(
        "module", help="Module directory name, for example log-analytics-workspace"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        context = AnalysisContext(
            source_root=root, trusted_root=root, module=args.module
        )
        client = GeminiClient(os.environ.get("GEMINI_API_KEY", ""))
        report = run_planner(context, client)
    except AgentError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    # JSON escapes newlines in model text, preventing it becoming workflow commands.
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
