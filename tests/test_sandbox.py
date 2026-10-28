"""Verify execution boundaries and fail-closed interpretation of Terraform JSON."""

import base64
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_agent.context import AgentError, AnalysisContext
from test_agent.hcl_policy import validate_proposal
from test_agent.sandbox import (
    DockerRunner,
    container_args,
    docker_environment,
    summarize,
)

ROOT = Path(__file__).resolve().parents[1]
HCL = (ROOT / "tests/fixtures/log_analytics_valid.tftest.hcl").read_text()
PROPOSAL = validate_proposal(HCL)


def events(*extra):
    base = [
        {"formatted_hcl_base64": base64.b64encode(HCL.encode()).decode()},
        {"runner_stage": "test"},
    ]
    return "\n".join(json.dumps(item) for item in [*base, *extra]).encode()


class SandboxTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("TF_AGENT_DOCKER_TESTS") == "1",
        "Set TF_AGENT_DOCKER_TESTS=1 after building the approved Docker runtime.",
    )
    def test_native_runner_formats_readonly_staged_tests(self):
        from test_agent.suite_runner import SuiteRunner

        original = (ROOT / "tests/fixtures/ai_search_valid.tftest.hcl").read_text()
        unformatted = original.replace("command = plan", "command=plan")
        self.assertNotEqual(original, unformatted)
        sources = {
            "modules/ai-search/main.tf": (
                ROOT / "tests/fixtures/ai_search_module/main.tf"
            ).read_text()
        }
        report, tests = SuiteRunner().execute(
            sources, {"tests/generated.tftest.hcl": unformatted}
        )
        self.assertTrue(report["passed"], report)
        self.assertNotEqual(tests["tests/generated.tftest.hcl"], unformatted)

        legacy_hcl = HCL.replace("command = plan", "command=plan")
        self.assertNotEqual(HCL, legacy_hcl)
        sources = {
            f"modules/log-analytics-workspace/{path.name}": path.read_text()
            for path in (ROOT / "tests/fixtures/log_analytics_module").glob("*.tf")
        }
        report, formatted = DockerRunner().execute(
            sources, validate_proposal(legacy_hcl)
        )
        self.assertTrue(report["passed"], report)
        self.assertNotEqual(formatted, legacy_hcl)

    def test_container_has_no_network_no_root_and_readonly_input(self):
        args = container_args("sha256:" + "a" * 64, ROOT, "controlled-name")
        for flag in (
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65532:65532",
            "--pull=never",
        ):
            self.assertIn(flag, args)
        self.assertNotIn("--privileged", args)
        self.assertNotIn("--env", args)
        self.assertIn("dst=/input,readonly", args[args.index("--mount") + 1])
        self.assertTrue(any("/work:rw,noexec" in arg for arg in args))

    def test_docker_cli_does_not_inherit_model_github_or_cloud_secrets(self):
        with patch.dict(
            os.environ,
            {
                "PATH": "test-path",
                "GEMINI_API_KEY": "private",
                "GITHUB_TOKEN": "private",
                "ARM_CLIENT_SECRET": "private",
            },
            clear=True,
        ):
            self.assertEqual(docker_environment(), {"PATH": "test-path"})

    def test_exit_zero_or_empty_summary_is_not_a_success(self):
        for extra in (
            [],
            [
                {
                    "type": "test_summary",
                    "test_summary": {
                        "status": "pass",
                        "passed": 0,
                        "failed": 0,
                        "errored": 0,
                        "skipped": 0,
                    },
                }
            ],
        ):
            result, _ = summarize(events(*extra), 0, PROPOSAL)
            self.assertFalse(result["passed"])

    def test_actual_runs_and_summary_are_both_required(self):
        complete = [
            {
                "type": "test_run",
                "test_run": {"run": name, "progress": "complete", "status": "pass"},
            }
            for name in PROPOSAL.runs
        ]
        summary = {
            "type": "test_summary",
            "test_summary": {
                "status": "pass",
                "passed": 3,
                "failed": 0,
                "errored": 0,
                "skipped": 0,
            },
        }
        self.assertTrue(summarize(events(*complete, summary), 0, PROPOSAL)[0]["passed"])
        self.assertFalse(
            summarize(events(*complete[:-1], summary), 0, PROPOSAL)[0]["passed"]
        )
        summary["test_summary"]["skipped"] = 1
        self.assertFalse(
            summarize(events(*complete, summary), 0, PROPOSAL)[0]["passed"]
        )

    def test_only_assertion_failure_can_count_as_detected_mutation(self):
        summary = {
            "type": "test_summary",
            "test_summary": {
                "status": "fail",
                "passed": 2,
                "failed": 1,
                "errored": 0,
                "skipped": 0,
            },
        }
        diagnostic = {
            "type": "diagnostic",
            "diagnostic": {
                "severity": "error",
                "summary": "Test assertion failed",
                "detail": "Retention differs.",
            },
        }
        self.assertTrue(
            summarize(events(summary, diagnostic), 1, PROPOSAL)[0]["assertion_failure"]
        )
        diagnostic["diagnostic"]["summary"] = "Provider failed"
        self.assertFalse(
            summarize(events(summary, diagnostic), 1, PROPOSAL)[0]["assertion_failure"]
        )

    def test_invalid_runner_stream_cannot_produce_success(self):
        for raw in (b"not json", b"[]"):
            with self.subTest(raw=raw), self.assertRaises(AgentError):
                summarize(raw, 0, PROPOSAL)

    def test_malformed_event_fields_are_controlled_failures(self):
        for event in (
            {"type": "test_run", "test_run": []},
            {"type": "test_summary", "test_summary": {"failed": "one"}},
            {"diagnostics": None},
        ):
            with self.subTest(event=event), self.assertRaises(AgentError):
                summarize(events(event), 0, PROPOSAL)

    def test_timeout_kills_cli_and_removes_only_owned_container(self):
        runner = object.__new__(DockerRunner)
        runner.image_id = "sha256:" + "a" * 64
        process = Mock()
        process.poll.return_value = None
        files = AnalysisContext(ROOT, ROOT, "log-analytics-workspace").files
        with (
            patch("test_agent.sandbox.subprocess.Popen", return_value=process),
            patch("test_agent.sandbox.time.monotonic", side_effect=[0, 121]),
            patch("test_agent.sandbox.docker") as docker,
        ):
            with self.assertRaisesRegex(AgentError, "wall time"):
                runner.execute(files, PROPOSAL)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
        args = docker.call_args.args[0]
        self.assertEqual(args[:2], ["rm", "--force"])
        self.assertRegex(args[2], r"^tf-test-agent-[a-f0-9]{32}$")
