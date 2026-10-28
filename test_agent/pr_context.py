"""Bounded PR snapshots from Git objects, without checking out PR code."""

import difflib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from test_agent.context import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_MODULE_BYTES,
    AgentError,
    AnalysisContext,
)
from test_agent.hcl_policy import MODULE, TEST_PATH, validate_sources
from test_agent.models import StrictModel

OID = r"^[0-9a-f]{40}$"
REPOSITORY = r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
TARGET = f"modules/{MODULE}/{TEST_PATH}"
MAX_GIT_BYTES = 1_048_576
SOURCE_PATH = re.compile(
    rf"modules/{MODULE}/(?:[A-Za-z0-9_.-]+\.tf|tests/[A-Za-z0-9_.-]+\.tftest\.hcl)"
)


class PRIdentity(StrictModel):
    repository: str = Field(pattern=REPOSITORY, max_length=200)
    number: int = Field(ge=1)
    base_sha: str = Field(pattern=OID)
    head_sha: str = Field(pattern=OID)
    origin: Literal["github", "local"] = "github"


class Change(StrictModel):
    status: Literal["A", "M", "D", "R", "T"]
    path: str = Field(min_length=1, max_length=250)
    previous_path: str | None = Field(default=None, max_length=250)

    @model_validator(mode="after")
    def check_paths(self):
        for path in (self.path, self.previous_path):
            if path is not None and (
                not path.isascii()
                or any(ord(c) < 32 for c in path)
                or "\\" in path
                or any(p in ("", ".", "..") for p in path.split("/"))
            ):
                raise ValueError("Unsupported repository path.")
        if (self.status == "R") != (self.previous_path is not None):
            raise ValueError("Only renames have a previous path.")
        return self


def check_snapshot(files):
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ValueError("Unsupported snapshot size.")
    if len({path.casefold() for path in files}) != len(files):
        raise ValueError("Case-colliding source paths.")
    total = 0
    for path, content in files.items():
        if not SOURCE_PATH.fullmatch(path) or not isinstance(content, str):
            raise ValueError("Unsupported snapshot file.")
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ValueError("Snapshot file exceeds its limit.")
        total += size
    if total > MAX_MODULE_BYTES:
        raise ValueError("Snapshot exceeds its total size limit.")


class PRBundle(StrictModel):
    identity: PRIdentity
    merge_base: str = Field(pattern=OID)
    changes: list[Change] = Field(max_length=128)
    selected_modules: list[str] = Field(max_length=32)
    reasons: list[str] = Field(max_length=32)
    before_files: dict[str, str]
    after_files: dict[str, str]

    @model_validator(mode="after")
    def check_files(self):
        check_snapshot(self.before_files)
        check_snapshot(self.after_files)
        selected, reasons = classify(self.changes)
        if selected != self.selected_modules:
            raise ValueError("Module selection does not match changed paths.")
        if any(reason not in self.reasons for reason in reasons):
            raise ValueError("An unsupported change was omitted.")
        return self

    @property
    def eligible(self):
        return MODULE in self.selected_modules and not self.reasons

    def analysis_context(self, trusted_root: Path):
        return AnalysisContext.from_snapshot(
            self.after_files,
            self.before_files,
            trusted_root,
            {
                **self.identity.model_dump(),
                "merge_base": self.merge_base,
                "changes": [change.model_dump() for change in self.changes],
                "diff": source_diff(self.before_files, self.after_files),
            },
        )


def source_diff(before, after):
    return "".join(
        "".join(
            difflib.unified_diff(
                before.get(path, "").splitlines(keepends=True),
                after.get(path, "").splitlines(keepends=True),
                fromfile=f"before/{path}",
                tofile=f"after/{path}",
            )
        )
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    )


def classify(changes):
    modules, reasons = set(), set()
    protected = ("test_agent/", "agent/", "skills/", "runtime/", ".github/", "scripts/")
    for change in changes:
        for path in (change.path, change.previous_path):
            if path is None:
                continue
            if path.startswith(protected) or path in (
                "pyproject.toml",
                "uv.lock",
                ".gitattributes",
                ".gitmodules",
                ".dockerignore",
            ):
                reasons.add(
                    "Agent policy, runtime or workflow changes require human review."
                )
            elif path.startswith("stacks/") and not path.endswith(".md"):
                reasons.add("Stack impact analysis is not supported yet.")
            elif path.endswith((".tf", ".tf.json")) and not path.startswith("modules/"):
                reasons.add("Root Terraform impact analysis is not supported yet.")
            elif path.startswith("modules/") and not path.endswith(".md"):
                parts = path.split("/")
                if len(parts) < 3 or not re.fullmatch(
                    r"[a-z][a-z0-9-]{0,63}", parts[1]
                ):
                    reasons.add("Unsupported module path.")
                    continue
                modules.add(parts[1])
                if parts[1] != MODULE:
                    reasons.add("Only log-analytics-workspace authoring is supported.")
                elif not SOURCE_PATH.fullmatch(path) or change.status == "T":
                    reasons.add("Unsupported module file or file-type change.")
    return sorted(modules), sorted(reasons)


class GitRepository:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def run(self, *args):
        # Do not inherit LLM/cloud credentials or user-supplied Git overrides.
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP"}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
        with tempfile.TemporaryFile() as output:
            try:
                result = subprocess.run(
                    [
                        "git",
                        "-c",
                        "core.hooksPath=",
                        "-c",
                        "core.fsmonitor=false",
                        "-C",
                        str(self.root),
                        *args,
                    ],
                    env=env,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise AgentError(
                    "Git is unavailable or exceeded its time limit."
                ) from None
            if result.returncode:
                raise AgentError("Could not read the requested Git revision.")
            output.seek(0)
            data = output.read(MAX_GIT_BYTES + 1)
            if len(data) > MAX_GIT_BYTES:
                raise AgentError("Git output exceeds its size limit.")
            return data

    def snapshot(self, revision):
        files = {}
        for entry in self.run(
            "ls-tree", "-rz", revision, "--", f"modules/{MODULE}/"
        ).split(b"\0"):
            if not entry:
                continue
            header, raw_path = entry.split(b"\t", 1)
            path = raw_path.decode("utf-8")
            if path.endswith(".md"):
                continue
            if not SOURCE_PATH.fullmatch(path):
                raise AgentError("Unsupported files exist inside the selected module.")
            mode, kind, oid = header.decode("ascii").split()
            if mode != "100644" or kind != "blob":
                raise AgentError(
                    "Linked, executable or submodule inputs are not allowed."
                )
            size = int(self.run("cat-file", "-s", oid))
            if size > MAX_FILE_BYTES or len(files) >= MAX_FILES:
                raise AgentError("The Git snapshot exceeds its input limits.")
            files[path] = self.run("cat-file", "blob", oid).decode("utf-8")
        check_snapshot(files)
        return files

    def collect(self, identity):
        for revision in (identity.base_sha, identity.head_sha):
            # A full validated OID is data, never a ref or a shell expression.
            self.run("cat-file", "-e", f"{revision}^{{commit}}")
        merge_base = (
            self.run("merge-base", identity.base_sha, identity.head_sha)
            .decode()
            .strip()
        )
        raw = (
            self.run(
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "-z",
                "--find-renames",
                merge_base,
                identity.head_sha,
                "--",
            )
            .decode("utf-8")
            .split("\0")
        )
        changes = []
        while raw and raw[0]:
            status = raw.pop(0)
            if status.startswith("R"):
                previous, path = raw.pop(0), raw.pop(0)
                changes.append(Change(status="R", path=path, previous_path=previous))
            else:
                changes.append(Change(status=status, path=raw.pop(0)))
            if len(changes) > 128:
                raise AgentError("The PR exceeds the 128-file change limit.")
        selected, reasons = classify(changes)
        before, after = {}, {}
        if MODULE in selected and not reasons:
            before, after = self.snapshot(merge_base), self.snapshot(identity.head_sha)
            if not before or not after:
                reasons.append("Added or removed modules require human review.")
            else:
                validate_sources(before)
                validate_sources(after)
                if any(
                    path.endswith(".tftest.hcl") and path != TARGET
                    for path in (*before, *after)
                ):
                    reasons.append("Additional test files require a broader verifier.")
        return PRBundle(
            identity=identity,
            merge_base=merge_base,
            changes=changes,
            selected_modules=selected,
            reasons=reasons,
            before_files=before,
            after_files=after,
        )
