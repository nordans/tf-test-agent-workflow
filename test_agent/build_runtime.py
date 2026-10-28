"""Build the trusted runtime before using the model; no API credentials needed."""

import subprocess
import sys
from pathlib import Path

from test_agent.sandbox import IMAGE, docker_environment


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            [
                "docker",
                "build",
                "--platform=linux/amd64",
                "--tag",
                IMAGE,
                "--file",
                str(root / "runtime" / "Dockerfile"),
                str(root),
            ],
            env=docker_environment(),
            timeout=600,
            check=False,
        )
        return result.returncode
    except (OSError, subprocess.TimeoutExpired):
        print("FAIL: could not build the Docker runtime.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
