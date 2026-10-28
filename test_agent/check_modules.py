"""Validate every module and stack against the approved offline provider schemas."""

import json
import re
import sys
import tempfile
import uuid
from pathlib import Path

import hcl2
from lark.exceptions import LarkError

from test_agent.context import MAX_FILE_BYTES, AgentError, read_text
from test_agent.hcl import OPTIONS
from test_agent.sandbox import (
    MAX_LOG_BYTES,
    container_args,
    decode_events,
    docker,
    runtime_id,
)

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_SMOKE = (
    "cp -R /opt/external-smoke/. /work/; "
    "cp /opt/provider/external.lock.hcl /work/.terraform.lock.hcl; "
    "terraform init -backend=false -input=false -lockfile=readonly -no-color "
    ">/work/init.log 2>&1 && terraform test -json"
)


def verify_external_mock(image, directory):
    name = "tf-external-mock-" + uuid.uuid4().hex
    args = container_args(image, directory, name)
    args[-1:-1] = ["--entrypoint", "/bin/sh"]
    try:
        result = docker([*args, "-c", EXTERNAL_SMOKE], timeout=120)
        if len(result.stdout) > MAX_LOG_BYTES:
            raise AgentError("External mock output exceeds its limit.")
        events = decode_events(result.stdout)
        summaries = [e["test_summary"] for e in events if "test_summary" in e]
        completed = [
            e["test_run"]
            for e in events
            if e.get("test_run", {}).get("progress") == "complete"
        ]
        passed = (
            result.returncode == 0
            and len(summaries) == 1
            and summaries[0].get("status") == "pass"
            and summaries[0].get("passed") == 1
            and all(
                summaries[0].get(key) == 0 for key in ("failed", "errored", "skipped")
            )
            and len(completed) == 1
            and completed[0].get("run") == "external_program_is_mocked"
            and completed[0].get("status") == "pass"
        )
        return {"passed": passed, "summaries": summaries}
    finally:
        docker(["rm", "--force", name])


def snapshot(root, destination):
    selected = []
    total = 0
    for parent in ("modules", "stacks"):
        directory = root / parent
        if directory.is_symlink() or directory.is_junction():
            raise AgentError("Linked module or stack directories are not allowed.")
        for module in sorted(directory.iterdir()):
            if not module.is_dir():
                continue
            if (
                module.is_symlink()
                or module.is_junction()
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", module.name)
            ):
                raise AgentError("Unsupported module or stack directory.")
            paths = sorted(module.glob("*.tf"))
            if not 1 <= len(paths) <= 32 or len(selected) >= 32:
                raise AgentError("Module validation exceeds its input limits.")
            relative = module.relative_to(root)
            target = destination / relative
            target.mkdir(parents=True)
            providers = set()
            for path in paths:
                content = read_text(path, root)
                total += len(content.encode("utf-8"))
                if total > MAX_FILE_BYTES * 32:
                    raise AgentError("The validation snapshot exceeds its total limit.")
                (target / path.name).write_text(content, encoding="utf-8", newline="\n")
                try:
                    configurations = hcl2.loads(
                        content, serialization_options=OPTIONS
                    ).get("terraform", [])
                except (LarkError, ValueError, RecursionError):
                    raise AgentError(
                        "Invalid Terraform source for validation."
                    ) from None
                for config in configurations:
                    for declarations in config.get("required_providers", []):
                        providers.update(declarations)
            if not providers or not providers <= {"azurerm", "external"}:
                raise AgentError("Validation requires approved provider declarations.")
            locks = "\n".join(
                read_text(ROOT / "runtime" / f"{provider}.lock.hcl", ROOT)
                for provider in sorted(providers)
            )
            (target / ".terraform.lock.hcl").write_text(
                locks, encoding="utf-8", newline="\n"
            )
            selected.append(relative.as_posix())
    return selected


def validate_repository(root=ROOT):
    image = runtime_id()
    reports = []
    with tempfile.TemporaryDirectory(prefix="tf-module-validation-") as directory:
        directory = Path(directory)
        directory.chmod(0o755)
        selected = snapshot(root, directory)
        for module in selected:
            name = "tf-module-validation-" + uuid.uuid4().hex
            args = container_args(image, directory, name)
            args[-1:-1] = ["--entrypoint", "/bin/sh"]
            try:
                result = docker([*args, "/opt/check-module.sh", module], timeout=120)
                if len(result.stdout) > MAX_LOG_BYTES:
                    raise AgentError("Module validation output exceeds its limit.")
                try:
                    report = json.loads(result.stdout)
                except (ValueError, UnicodeError):
                    raise AgentError(
                        "Module validation returned invalid JSON."
                    ) from None
                if (
                    not isinstance(report, dict)
                    or type(report.get("valid")) is not bool
                ):
                    raise AgentError("Module validation returned malformed results.")
                report["path"] = module
                report["valid"] = result.returncode == 0 and report.get("valid") is True
                reports.append(report)
            finally:
                docker(["rm", "--force", name])
        external_mock = verify_external_mock(image, directory)
    return {
        "image_id": image,
        "passed": bool(reports)
        and all(r["valid"] for r in reports)
        and external_mock["passed"],
        "modules": reports,
        "external_mock": external_mock,
    }


def main():
    try:
        report = validate_repository()
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    except AgentError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
