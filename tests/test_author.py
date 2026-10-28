"""Exercise author permissions and failure handling without an API or Docker."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_module_planner import FakeClient, tool_calls

from test_agent.__main__ import artifact_directory
from test_agent.author import run_author
from test_agent.context import AgentError, AnalysisContext

ROOT = Path(__file__).resolve().parents[1]
HCL = (ROOT / "tests/fixtures/log_analytics_valid.tftest.hcl").read_text()
LOAD = tool_calls(("load_skill", {"name": "terraform-unit-testing"}))
PROPOSE = tool_calls(("propose_test_file", {"content": HCL}))
VERIFY = tool_calls(("verify_module", {}))
FINISH = tool_calls(("submit_result", {}))


def result(*, passed=False, assertion=False, executed=True):
    return {
        "passed": passed,
        "terraform": {
            "assertion_failure": assertion,
            "runs": [{"name": "defaults", "status": "fail" if assertion else "pass"}]
            if executed
            else [],
        },
    }


class AuthorTests(unittest.TestCase):
    def setUp(self):
        self.context = AnalysisContext(ROOT, ROOT, "log-analytics-workspace")
        # Authoring starts before the reviewed proposal is copied into the module.
        self.context.files = {
            path: text
            for path, text in self.context.files.items()
            if path.endswith(".tf")
        }
        planner = patch(
            "test_agent.author.run_planner",
            return_value={"plan": {"decision": "add_tests"}},
        )
        planner.start()
        self.addCleanup(planner.stop)

    def run_author(self, responses, verification):
        client = FakeClient(copy.deepcopy(responses))
        runner = Mock()
        runner.verify.return_value = (verification, HCL)
        report, text = run_author(self.context, client, runner)
        return report, client, runner

    def test_tools_are_gated_and_execution_status_comes_from_runner(self):
        report, client, runner = self.run_author(
            [LOAD, PROPOSE, VERIFY, FINISH], result(passed=True)
        )
        self.assertTrue(report["passed"])
        self.assertTrue(report["tests_executed"])
        self.assertFalse(report["requires_review"])
        runner.verify.assert_called_once()
        self.assertEqual(
            [
                [tool["name"] for tool in request["tools"]]
                for request in client.requests
            ],
            [
                ["load_skill"],
                ["propose_test_file"],
                ["verify_module"],
                ["submit_result"],
            ],
        )

    def test_normal_assertion_failure_ends_repairs_and_requires_review(self):
        report, client, _ = self.run_author(
            [LOAD, PROPOSE, VERIFY, FINISH], result(assertion=True)
        )
        self.assertFalse(report["passed"])
        self.assertTrue(report["requires_review"])
        self.assertEqual(len(report["attempts"]), 1)
        self.assertEqual(
            [tool["name"] for tool in client.requests[-1]["tools"]], ["submit_result"]
        )
        with self.assertRaises(AgentError):
            self.run_author([LOAD, PROPOSE, VERIFY, PROPOSE], result(assertion=True))

    def test_repair_cannot_remove_assertions_or_change_their_inputs(self):
        for replacement in (
            HCL.replace("== 30", "== 31", 1),
            HCL.replace("retention_in_days = 90", "retention_in_days = 30"),
        ):
            changed = tool_calls(("propose_test_file", {"content": replacement}))
            report, _, runner = self.run_author(
                [LOAD, PROPOSE, VERIFY, changed, FINISH], result(executed=False)
            )
            self.assertFalse(report["passed"])
            self.assertFalse(report["tests_executed"])
            self.assertIn(
                "ASSERTION_REMOVAL_OR_CHANGE", report["attempts"][1]["policy"]["error"]
            )
            runner.verify.assert_called_once()

    def test_only_two_proposals_and_rejected_hcl_never_runs(self):
        invalid = tool_calls(("propose_test_file", {"content": "invalid {{{"}))
        report, client, runner = self.run_author(
            [LOAD, invalid, invalid, FINISH], result()
        )
        self.assertFalse(report["passed"])
        self.assertFalse(report["tests_executed"])
        self.assertEqual(len(report["attempts"]), 2)
        self.assertEqual(
            [tool["name"] for tool in client.requests[-1]["tools"]], ["submit_result"]
        )
        runner.verify.assert_not_called()

    def test_model_cannot_supply_commands_paths_or_execution_claims(self):
        for name, args, preceding in (
            ("verify_module", {"command": "terraform apply"}, [LOAD, PROPOSE]),
            ("propose_test_file", {"content": HCL, "path": "main.tf"}, [LOAD]),
            ("submit_result", {"passed": True}, [LOAD, PROPOSE, VERIFY]),
        ):
            with self.subTest(name=name), self.assertRaises(AgentError):
                self.run_author(
                    [*preceding, tool_calls((name, args))], result(passed=True)
                )

    def test_existing_artifacts_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = artifact_directory(root, "log-analytics-workspace")
            output.mkdir(parents=True)
            (output / "report.json").write_text("previous evidence")
            with self.assertRaises(AgentError):
                artifact_directory(root, "log-analytics-workspace")
