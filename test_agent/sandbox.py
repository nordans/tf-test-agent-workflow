"""Execute approved tests in a networkless, secret-free Linux container."""

import base64
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from test_agent.context import AgentError, sha256_text
from test_agent.hcl_policy import MODULE, Proposal, validate_proposal, validate_sources

IMAGE = "tf-test-agent-runtime:tf1.17.1-azurerm4.81.0"
MAX_LOG_BYTES = 1_048_576
WALL_SECONDS = 120


def docker_environment() -> dict[str, str]:
    # The Docker CLI needs its normal local connection settings, not agent secrets.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "APPDATA",
        "LOCALAPPDATA",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "DOCKER_CERT_PATH",
        "DOCKER_TLS_VERIFY",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def docker(args: list[str], *, timeout=30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *args],
            env=docker_environment(),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AgentError("Docker is unavailable or did not respond in time.") from None


def runtime_id() -> str:
    platform = docker(["info", "--format", "{{.OSType}}/{{.Architecture}}"])
    if platform.returncode or platform.stdout.strip() not in (
        b"linux/x86_64",
        b"linux/amd64",
    ):
        raise AgentError("Start an amd64 Linux Docker engine before verification.")
    image = docker(["image", "inspect", IMAGE, "--format", "{{.Id}}"])
    identity = image.stdout.decode("ascii", errors="replace").strip()
    if image.returncode or not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
        raise AgentError(
            "Build the approved runtime first: python -m test_agent.build_runtime"
        )
    return identity


def container_args(identity: str, directory: Path, name: str) -> list[str]:
    return [
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65532:65532",
        "--cpus=1",
        "--memory=1536m",
        "--pids-limit=64",
        "--tmpfs",
        "/work:rw,noexec,nosuid,nodev,size=128m,uid=65532,gid=65532,mode=0700",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532,mode=0700",
        "--mount",
        f"type=bind,src={directory.resolve()},dst=/input,readonly",
        identity,
    ]


def decode_events(raw: bytes) -> list[dict]:
    try:
        text, decoder, events = raw.decode("utf-8"), json.JSONDecoder(), []
        while text.strip():
            event, end = decoder.raw_decode(text.lstrip())
            if not isinstance(event, dict):
                raise ValueError
            events.append(event)
            text = text.lstrip()[end:]
        return events
    except (ValueError, UnicodeError):
        raise AgentError("Sandbox returned an invalid JSON event stream.") from None


def summarize(raw: bytes, returncode: int, proposal: Proposal) -> tuple[dict, str]:
    events = decode_events(raw)
    stage = "startup"
    formatted = None
    runs, summaries, diagnostics = [], [], []
    for event in events:
        for key in ("test_run", "test_summary", "diagnostic"):
            if key in event and not isinstance(event[key], dict):
                raise AgentError("Sandbox returned a malformed Terraform event.")
        if "diagnostics" in event and not isinstance(event["diagnostics"], list):
            raise AgentError("Sandbox returned malformed diagnostics.")
        if event.get("runner_stage") in ("format", "init", "validate", "test"):
            stage = event["runner_stage"]
        if "formatted_hcl_base64" in event:
            try:
                formatted = base64.b64decode(
                    event["formatted_hcl_base64"], validate=True
                ).decode("utf-8")
            except (ValueError, UnicodeError, TypeError):
                raise AgentError("Sandbox returned invalid formatted HCL.") from None
        if event.get("type") == "test_run":
            run = event.get("test_run", {})
            if run.get("progress") == "complete":
                runs.append({"name": run.get("run"), "status": run.get("status")})
        if event.get("type") == "test_summary":
            summaries.append(event.get("test_summary", {}))
        candidates = event.get("diagnostics", [])
        if event.get("type") == "diagnostic":
            candidates = [*candidates, event.get("diagnostic", {})]
        for diagnostic in candidates:
            if isinstance(diagnostic, dict) and diagnostic.get("severity") == "error":
                diagnostics.append(
                    {
                        "summary": str(diagnostic.get("summary", ""))[:200],
                        "detail": str(diagnostic.get("detail", ""))[:1000],
                    }
                )
    if formatted is not None:
        checked = validate_proposal(formatted)
        if checked.runs != proposal.runs:
            raise AgentError("Formatting changed the declared test runs.")
    summary = summaries[0] if len(summaries) == 1 else {}
    if summary and any(
        type(summary.get(field)) is not int or summary[field] < 0
        for field in ("passed", "failed", "errored", "skipped")
    ):
        raise AgentError("Sandbox returned invalid test counts.")
    passed = (
        returncode == 0
        and stage == "test"
        and formatted is not None
        and summary.get("status") == "pass"
        and summary.get("passed") == len(proposal.runs)
        and all(summary.get(field) == 0 for field in ("failed", "errored", "skipped"))
        and len(runs) == len(proposal.runs)
        and {run["name"] for run in runs} == set(proposal.runs)
        and all(run["status"] == "pass" for run in runs)
        and not diagnostics
    )
    assertion_failure = (
        returncode != 0
        and stage == "test"
        and summary.get("failed", 0) > 0
        and summary.get("errored") == 0
        and any(item["summary"] == "Test assertion failed" for item in diagnostics)
    )
    return {
        "passed": passed,
        "stage": stage,
        "runs": runs,
        "summary": summary,
        "assertion_failure": assertion_failure,
        "diagnostics": diagnostics[:8],
    }, formatted or proposal.hcl


class DockerRunner:
    def __init__(self):
        self.image_id = runtime_id()

    def execute(self, files: dict[str, str], proposal: Proposal) -> tuple[dict, str]:
        validate_sources(files)
        name = "tf-test-agent-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="tf-test-agent-") as directory:
            directory = Path(directory)
            directory.chmod(0o755)
            for source, text in files.items():
                if source.endswith(".tf"):
                    path = directory / Path(source).name
                    path.write_text(text, encoding="utf-8", newline="\n")
                    path.chmod(0o444)
            test_file = directory / "generated.tftest.hcl"
            test_file.write_text(proposal.hcl, encoding="utf-8", newline="\n")
            test_file.chmod(0o444)
            with tempfile.TemporaryFile() as output:
                process = None
                try:
                    process = subprocess.Popen(
                        ["docker", *container_args(self.image_id, directory, name)],
                        env=docker_environment(),
                        stdout=output,
                        stderr=output,
                    )
                    deadline = time.monotonic() + WALL_SECONDS
                    while process.poll() is None:
                        if (
                            time.monotonic() >= deadline
                            or os.fstat(output.fileno()).st_size > MAX_LOG_BYTES
                        ):
                            raise AgentError(
                                "Sandbox exceeded its wall time or output limit."
                            )
                        try:
                            process.wait(timeout=0.1)
                        except subprocess.TimeoutExpired:
                            pass
                    output.seek(0)
                    raw = output.read(MAX_LOG_BYTES + 1)
                    if len(raw) > MAX_LOG_BYTES:
                        raise AgentError("Sandbox output exceeds the size limit.")
                    if not raw.strip().startswith(b"{"):
                        raise AgentError(
                            "Docker failed before producing sandbox events."
                        )
                    return summarize(raw, process.returncode, proposal)
                except OSError:
                    raise AgentError("Could not start the Docker sandbox.") from None
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
                    docker(["rm", "--force", name])

    def verify(self, files: dict[str, str], proposal: Proposal) -> tuple[dict, str]:
        result, formatted = self.execute(files, proposal)
        report = {
            "image_id": self.image_id,
            "terraform": result,
            "mutation": {"executed": False},
            "passed": False,
        }
        if result["passed"]:
            mutated = dict(files)
            path = f"modules/{MODULE}/main.tf"
            mutated[path], count = re.subn(
                r"(?m)^(\s*retention_in_days\s*=\s*)var\.retention_in_days(\s*)$",
                r"\g<1>30\2",
                files[path],
            )
            if count != 1:
                raise AgentError(
                    "Retention mutation requires exactly one supported variable reference."
                )
            mutation, _ = self.execute(mutated, validate_proposal(formatted))
            report["mutation"] = {
                "executed": True,
                "description": "Replace retention variable wiring with constant 30 in a temporary copy.",
                "detected": mutation["assertion_failure"],
                "terraform": mutation,
            }
            report["passed"] = mutation["assertion_failure"]
        report["proposal_sha256"] = sha256_text(formatted)
        return report, formatted
