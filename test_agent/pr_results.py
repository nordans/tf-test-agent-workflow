"""Strict stage handoffs; the model never supplies execution status."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from test_agent.context import AgentError, sha256_text
from test_agent.models import StrictModel, TestPlan
from test_agent.pr_context import PRIdentity

MAX_JSON_BYTES = 1_048_576


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


class Draft(StrictModel):
    identity: PRIdentity
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["proposed", "no_change", "needs_review", "skipped"]
    analysis: Analysis | None = None
    hcl: str | None = Field(default=None, max_length=32768)
    tool_calls: list[str] = Field(default_factory=list, max_length=4)
    usage: dict[str, int] = Field(default_factory=dict)
    policy: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)


class Verification(StrictModel):
    identity: PRIdentity
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal[
        "verified", "no_change_verified", "needs_review", "skipped", "failed"
    ]
    baseline: dict | None = None
    existing: dict | None = None
    candidate: dict | None = None
    hcl: str | None = Field(default=None, max_length=32768)
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def check_verified_result(self):
        if self.status in ("verified", "no_change_verified"):
            candidate = self.candidate or {}
            if (
                self.hcl is None
                or candidate.get("passed") is not True
                or not isinstance(candidate.get("terraform"), dict)
                or not isinstance(candidate.get("mutation"), dict)
                or candidate.get("terraform", {}).get("passed") is not True
                or candidate.get("mutation", {}).get("detected") is not True
                or candidate.get("proposal_sha256") != sha256_text(self.hcl)
                or (
                    self.baseline is not None
                    and self.baseline.get("passed") is not True
                )
            ):
                raise ValueError(
                    "A verified result requires passing execution and mutation evidence."
                )
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(model.model_dump(), file, indent=2, ensure_ascii=True)
        file.write("\n")
