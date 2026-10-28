"""Run complete module suites and bounded source mutations with approved providers."""

import base64
import json
import re
import tempfile
from pathlib import Path

import hcl2

from test_agent.context import AgentError, read_text, sha256_text
from test_agent.sandbox import decode_events, run_container, runtime_id
from test_agent.terraform_policy import (
    canonical,
    literal,
    validate_sources,
    validate_suite,
)

ROOT = Path(__file__).resolve().parents[1]


def suite_hash(files):
    return sha256_text(canonical(files))


def summarize(raw, returncode, suite, sources, schemas):
    events = decode_events(raw)
    formatted, runs, summaries, diagnostics, stage = {}, [], [], [], "startup"
    for event in events:
        for key in ("test_run", "test_summary", "diagnostic"):
            if key in event and not isinstance(event[key], dict):
                raise AgentError("Malformed Terraform suite event.")
        if "runner_stage" in event:
            stage = event["runner_stage"]
        if "formatted_test" in event:
            path = event["formatted_test"]
            if path not in suite.files or path in formatted:
                raise AgentError("Unexpected formatted test path.")
            try:
                formatted[path] = base64.b64decode(
                    event["content_base64"], validate=True
                ).decode("utf-8")
            except (ValueError, KeyError, UnicodeError, TypeError):
                raise AgentError("Invalid formatted test content.") from None
        if (
            event.get("type") == "test_run"
            and event["test_run"].get("progress") == "complete"
        ):
            run = event["test_run"]
            runs.append(
                {
                    "file": run.get("path", event.get("@testfile")),
                    "name": run.get("run"),
                    "status": run.get("status"),
                }
            )
        if event.get("type") == "test_summary":
            summaries.append(event["test_summary"])
        candidates = event.get("diagnostics", [])
        if not isinstance(candidates, list):
            raise AgentError("Malformed validation diagnostics.")
        if event.get("type") == "diagnostic":
            candidates = [*candidates, event["diagnostic"]]
        for error in candidates:
            if isinstance(error, dict) and error.get("severity") == "error":
                diagnostics.append(
                    {
                        "summary": str(error.get("summary", ""))[:200],
                        "detail": str(error.get("detail", ""))[:1000],
                    }
                )
    if set(formatted) == set(suite.files):
        checked = validate_suite(sources, formatted, schemas)
        if checked.runs != suite.runs:
            raise AgentError("Formatting changed suite runs.")
    summary = summaries[0] if len(summaries) == 1 else {}
    if summary and any(
        type(summary.get(k)) is not int or summary[k] < 0
        for k in ("passed", "failed", "errored", "skipped")
    ):
        raise AgentError("Invalid Terraform suite counts.")
    passed = (
        returncode == 0
        and stage == "test"
        and set(formatted) == set(suite.files)
        and summary.get("status") == "pass"
        and summary.get("passed") == len(suite.runs)
        and all(summary.get(k) == 0 for k in ("failed", "errored", "skipped"))
        and len(runs) == len(suite.runs)
        and {(r["file"], r["name"]) for r in runs} == set(suite.runs)
        and all(r["status"] == "pass" for r in runs)
        and not diagnostics
    )
    assertion_failure = (
        returncode != 0
        and stage == "test"
        and summary.get("failed", 0) > 0
        and summary.get("errored") == 0
        and any(d["summary"] == "Test assertion failed" for d in diagnostics)
    )
    return {
        "passed": passed,
        "stage": stage,
        "runs": runs,
        "summary": summary,
        "assertion_failure": assertion_failure,
        "diagnostics": diagnostics[:8],
    }, formatted if set(formatted) == set(suite.files) else suite.files


def source_mutations(files, suite):
    source = validate_sources(files)
    defaults = {
        name: body["default"]
        for name, body in source.variables.items()
        if "default" in body and literal(body["default"])
    }
    inputs, variants = dict(defaults), {}
    for data in suite.parsed.values():
        global_values = {
            k: v for values in data.get("variables", []) for k, v in values.items()
        }
        inputs.update(global_values)
        for run in data["run"]:
            body = next(iter(run.values()))
            effective = {
                **defaults,
                **global_values,
                **{
                    k: v
                    for values in body.get("variables", [])
                    for k, v in values.items()
                },
            }
            for name, value in effective.items():
                variants.setdefault(name, set()).add(canonical(value))
                inputs.setdefault(name, value)
    candidates = []
    seen = set()
    conditions = "\n".join(
        a["condition"]
        for data in suite.parsed.values()
        for run in data["run"]
        for body in run.values()
        for a in body.get("assert", [])
    )
    for path, text in sorted(files.items()):
        if not path.endswith(".tf"):
            continue
        tree = hcl2.parses_to_tree(text)
        for block in tree.find_data("block"):
            if str(block.children[0].children[0]) != "resource":
                continue
            kind, resource_name = (
                text[node.meta.start_pos : node.meta.end_pos].strip('"')
                for node in block.children[1:3]
            )
            address = f"{kind}.{resource_name}"
            for attribute in block.find_data("attribute"):
                key = str(attribute.children[0].children[0])
                expression = attribute.children[2]
                snippet = text[expression.meta.start_pos : expression.meta.end_pos]
                variable = re.fullmatch(r"var\.([A-Za-z_][A-Za-z0-9_]*)", snippet)
                if variable and variable[1] in inputs and literal(inputs[variable[1]]):
                    name = variable[1]
                    replacement = json.dumps(defaults.get(name, inputs[name]))
                    if key == "for_each" and isinstance(
                        defaults.get(name, inputs[name]), list
                    ):
                        replacement = f"toset({replacement})"
                    priority = 0 if len(variants.get(name, set())) > 1 else 1
                    description = (
                        f"Freeze {key} wiring from var.{name} to a known literal input."
                    )
                elif snippet in ("true", "false"):
                    replacement = "false" if snippet == "true" else "true"
                    priority, description = 2, f"Flip configured Boolean {key}."
                else:
                    continue
                location = (path, expression.meta.start_pos, expression.meta.end_pos)
                if location in seen:
                    continue
                seen.add(location)
                mutated = dict(files)
                mutated[path] = text[: location[1]] + replacement + text[location[2] :]
                referenced = f"{address}.{key}" in conditions or (
                    key in ("for_each", "count") and address in conditions
                )
                candidates.append(
                    (
                        (0 if referenced else 1, priority),
                        path,
                        location[1],
                        description,
                        mutated,
                    )
                )
    for _, path, _, description, mutated in sorted(
        candidates, key=lambda item: item[:3]
    )[:3]:
        yield path, description, mutated


class SuiteRunner:
    def __init__(self):
        self.image_id = runtime_id()
        with tempfile.TemporaryDirectory(prefix="tf-provider-schemas-") as directory:
            directory = Path(directory)
            directory.chmod(0o755)
            raw, code = run_container(
                self.image_id,
                directory,
                script="/opt/provider-schemas.sh",
                max_bytes=67108864,
            )
        try:
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or data.get("format_version", "").split(".")[0] != "1"
            ):
                raise ValueError
            if code or set(data["provider_schemas"]) != {
                "registry.terraform.io/hashicorp/azurerm",
                "registry.terraform.io/hashicorp/external",
            }:
                raise ValueError
            self.schemas = data["provider_schemas"]
        except (ValueError, KeyError, TypeError):
            raise AgentError("Could not read approved provider schemas.") from None
        self.schema_sha256 = sha256_text(canonical(self.schemas))

    def execute(self, sources, tests):
        suite = validate_suite(sources, tests, self.schemas)
        source = validate_sources(sources)
        with tempfile.TemporaryDirectory(prefix="tf-suite-") as directory:
            directory = Path(directory)
            directory.chmod(0o755)
            for path, content in sources.items():
                if path.endswith(".tf"):
                    target = directory / Path(path).name
                    target.write_text(content, encoding="utf-8", newline="\n")
                    target.chmod(0o444)
            (directory / "tests").mkdir()
            (directory / "tests").chmod(0o755)
            for path, content in tests.items():
                target = directory / path
                target.write_text(content, encoding="utf-8", newline="\n")
                target.chmod(0o444)
            locks = "\n".join(
                read_text(ROOT / "runtime" / f"{p}.lock.hcl", ROOT)
                for p in sorted(source.providers)
            )
            (directory / ".terraform.lock.hcl").write_text(
                locks, encoding="utf-8", newline="\n"
            )
            (directory / ".terraform.lock.hcl").chmod(0o444)
            raw, code = run_container(
                self.image_id, directory, script="/opt/verify-suite.sh"
            )
        return summarize(raw, code, suite, sources, self.schemas)

    def verify(self, sources, tests):
        positive, formatted = self.execute(sources, tests)
        controls = []
        if positive["passed"]:
            suite = validate_suite(sources, formatted, self.schemas)
            for path, description, mutated in source_mutations(sources, suite):
                try:
                    result, _ = self.execute(mutated, formatted)
                    controls.append(
                        {
                            "path": path,
                            "description": description,
                            "detected": result["assertion_failure"],
                            "terraform": result,
                        }
                    )
                except AgentError as error:
                    controls.append(
                        {
                            "path": path,
                            "description": description,
                            "detected": False,
                            "error": str(error),
                        }
                    )
                if controls[-1]["detected"]:
                    break
        return {
            "image_id": self.image_id,
            "terraform": positive,
            "mutation": {
                "executed": bool(controls),
                "detected": any(c["detected"] for c in controls),
                "attempts": controls,
            },
            "passed": positive["passed"] and any(c["detected"] for c in controls),
            "suite_sha256": suite_hash(formatted),
        }, formatted
