"""Strict, revision-bound per-module stage artifacts; no model execution claims."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from test_agent.context import AgentError, sha256_text
from test_agent.models import StrictModel, TestPlan
from test_agent.pr_context import PRIdentity
from test_agent.terraform_policy import TEST_PATH, canonical

MAX_JSON_BYTES = 1_048_576
MODULE_NAME = r"^[a-z][a-z0-9-]{0,63}$"


def same_test_content(left: str | None, right: str | None) -> bool:
    return (
        left is not None
        and right is not None
        and left.replace("\r\n", "\n") == right.replace("\r\n", "\n")
    )


def same_suites(left, right):
    return set(left) == set(right) and all(
        same_test_content(left[p], right[p]) for p in left
    )


def suite_hash(files):
    return sha256_text(canonical(files))


class Analysis(StrictModel):
    mode: Literal["analysis_only"]
    module: str
    model: str
    tests_executed: Literal[False]
    tool_calls: list[str]
    existing_test_files: list[str]
    provenance: dict
    usage: dict[str, int]
    plan: TestPlan


def check_tests(files):
    if len(files) > 8 or any(
        not TEST_PATH.fullmatch(p)
        or not isinstance(t, str)
        or len(t.encode("utf-8")) > 16384
        for p, t in files.items()
    ):
        raise ValueError("Invalid test artifact paths or size.")
    if len({p.casefold() for p in files}) != len(files):
        raise ValueError("Case-colliding test artifact paths.")


class ModuleDraft(StrictModel):
    module: str = Field(pattern=MODULE_NAME)
    status: Literal["proposed", "no_change", "needs_review", "skipped"]
    analysis: Analysis | None = None
    edits: dict[str, str] = Field(default_factory=dict)
    tool_calls: list[str] = Field(default_factory=list, max_length=4)
    usage: dict[str, int] = Field(default_factory=dict)
    policy: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def check_module(self):
        check_tests(self.edits)
        if self.analysis is not None and self.analysis.module != self.module:
            raise ValueError("Analysis targets a different module.")
        if (self.status == "proposed") != bool(self.edits):
            raise ValueError("Only a proposed draft may contain edits.")
        return self


class Draft(StrictModel):
    identity: PRIdentity
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    modules: list[ModuleDraft] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=2000)

    @property
    def status(self):
        states = {item.status for item in self.modules}
        if "proposed" in states:
            return "proposed"
        if "no_change" in states:
            return "no_change"
        if "needs_review" in states:
            return "needs_review"
        return "skipped"

    @model_validator(mode="after")
    def unique_modules(self):
        if len({m.module for m in self.modules}) != len(self.modules):
            raise ValueError("Duplicate module draft.")
        return self


class ModuleVerification(StrictModel):
    module: str = Field(pattern=MODULE_NAME)
    status: Literal[
        "verified", "no_change_verified", "needs_review", "skipped", "failed"
    ]
    baseline: dict | None = None
    existing: dict | None = None
    candidate: dict | None = None
    tests: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def checked_result(self):
        check_tests(self.tests)
        if self.status in ("verified", "no_change_verified"):
            candidate = self.candidate or {}
            if (
                not self.tests
                or candidate.get("passed") is not True
                or not isinstance(candidate.get("terraform"), dict)
                or not isinstance(candidate.get("mutation"), dict)
                or candidate.get("terraform", {}).get("passed") is not True
                or candidate.get("mutation", {}).get("detected") is not True
                or candidate.get("suite_sha256") != suite_hash(self.tests)
                or (
                    self.baseline is not None
                    and self.baseline.get("passed") is not True
                )
                or (
                    self.status == "no_change_verified"
                    and (self.existing or {}).get("passed") is not True
                )
            ):
                raise ValueError(
                    "Verified module requires execution and source-mutation evidence."
                )
        return self


def report_status(modules):
    states = {item.status for item in modules}
    if "failed" in states:
        return "failed"
    if "needs_review" in states:
        return "needs_review"
    if states and states <= {"verified", "no_change_verified"}:
        return "verified" if "verified" in states else "no_change_verified"
    return "needs_review" if states - {"skipped"} else "skipped"


class Verification(StrictModel):
    identity: PRIdentity
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal[
        "verified", "no_change_verified", "needs_review", "skipped", "failed"
    ]
    modules: list[ModuleVerification] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=2000)
    review_reasons: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def check_aggregate(self):
        if len({m.module for m in self.modules}) != len(self.modules):
            raise ValueError("Duplicate module report.")
        expected = report_status(self.modules)
        if self.review_reasons and expected != "failed":
            expected = "needs_review"
        if self.status != "failed" and self.status != expected:
            raise ValueError("Overall status does not match module evidence.")
        return self


def digest(model):
    return sha256_text(
        json.dumps(model.model_dump(), sort_keys=True, ensure_ascii=True)
    )


def read_json(path: Path):
    if path.is_symlink() or path.is_junction():
        raise AgentError("Linked JSON inputs are not allowed.")
    try:
        with path.open("rb") as file:
            data = file.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise AgentError("JSON input exceeds its size limit.")
        return json.loads(data)
    except (OSError, ValueError):
        raise AgentError("Could not read the bounded JSON input.") from None


def write_json(path: Path, model):
    data = json.dumps(model.model_dump(), indent=2, ensure_ascii=True) + "\n"
    if len(data.encode("utf-8")) > MAX_JSON_BYTES:
        raise AgentError("JSON output exceeds its size limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write(data)
