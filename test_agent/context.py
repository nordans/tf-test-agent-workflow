"""Read a bounded source snapshot; model arguments never become file paths."""

import hashlib
import re
from pathlib import Path

import yaml

SKILL_NAME = "terraform-change-analysis"
MAX_FILE_BYTES = 16_384
MAX_MODULE_BYTES = 98_304
MAX_FILES = 32


class AgentError(Exception):
    """A controlled failure whose message is safe to display."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path, root: Path) -> str:
    if (
        path.is_symlink()
        or path.is_junction()
        or not path.resolve().is_relative_to(root)
    ):
        raise AgentError("Refusing a linked file or a path outside the allowed root.")
    try:
        with path.open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise AgentError("An input file exceeds the size limit.")
        return data.decode("utf-8")
    except (OSError, UnicodeError):
        raise AgentError("Could not read an allowed UTF-8 input file.") from None


class AnalysisContext:
    def __init__(self, source_root: Path, trusted_root: Path, module: str):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", module):
            raise AgentError("Select one module by directory name, not by path.")
        self.module = module
        source_root, trusted_root = source_root.resolve(), trusted_root.resolve()
        module_dir = source_root / "modules" / module
        for directory in (source_root / "modules", module_dir, module_dir / "tests"):
            if directory.is_symlink() or directory.is_junction():
                raise AgentError("Linked module or test directories are not allowed.")
        if not module_dir.is_dir():
            raise AgentError("The selected module does not exist.")
        paths = sorted(module_dir.glob("*.tf")) + sorted(
            (module_dir / "tests").glob("*.tftest.hcl")
        )
        if not paths or len(paths) > MAX_FILES:
            raise AgentError(
                "The module must contain between 1 and 32 supported files."
            )
        self.files = {
            path.relative_to(source_root).as_posix(): read_text(path, source_root)
            for path in paths
        }
        if (
            sum(len(text.encode("utf-8")) for text in self.files.values())
            > MAX_MODULE_BYTES
        ):
            raise AgentError("The module snapshot exceeds the total size limit.")

        self.instructions = read_text(
            trusted_root / "agent" / "instructions.md", trusted_root
        )
        self.skill = read_text(
            trusted_root / "skills" / SKILL_NAME / "SKILL.md", trusted_root
        )
        try:
            _, header, _ = self.skill.split("---", 2)
            metadata = yaml.safe_load(header)
            if metadata["name"] != SKILL_NAME or not isinstance(
                metadata["description"], str
            ):
                raise ValueError
            self.skill_description = metadata["description"]
        except (ValueError, KeyError, TypeError, yaml.YAMLError):
            raise AgentError(
                "The trusted analysis skill has invalid metadata."
            ) from None

    def module_data(self) -> dict:
        return {
            "module": self.module,
            "files": [
                {
                    "path": path,
                    "numbered_source": "\n".join(
                        f"{line}: {text}"
                        for line, text in enumerate(content.splitlines(), start=1)
                    ),
                }
                for path, content in self.files.items()
            ],
            "existing_test_files": [
                path for path in self.files if path.endswith(".tftest.hcl")
            ],
        }

    def validate_evidence(self, plan) -> None:
        for item in [*plan.test_cases, *plan.findings]:
            for evidence in item.evidence:
                source = self.files.get(evidence.file)
                if source is None or evidence.line > len(source.splitlines()):
                    raise AgentError(
                        "Plan evidence must reference a supplied file and an existing line."
                    )

    def provenance(self) -> dict:
        return {
            "agent_sha256": sha256_text(self.instructions),
            "skill": {"name": SKILL_NAME, "sha256": sha256_text(self.skill)},
            "files": [
                {"path": path, "sha256": sha256_text(content)}
                for path, content in self.files.items()
            ],
        }
