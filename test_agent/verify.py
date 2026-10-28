"""Verify the checked-in proposal without any model access or API credentials."""

import json
import sys
from pathlib import Path

from test_agent.context import AgentError, AnalysisContext
from test_agent.hcl_policy import MODULE, TEST_PATH, validate_proposal, validate_sources
from test_agent.sandbox import DockerRunner


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        context = AnalysisContext(root, root, MODULE)
        validate_sources(context.files)
        text = context.files.get(f"modules/{MODULE}/{TEST_PATH}")
        if text is None:
            raise AgentError("The reviewed generated test file is missing.")
        report, _ = DockerRunner().verify(context.files, validate_proposal(text))
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0 if report["passed"] else 1
    except AgentError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
