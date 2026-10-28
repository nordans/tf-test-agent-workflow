"""Offline checks for read boundaries, tool permissions and plan validation."""

import copy
import tempfile
import unittest
from pathlib import Path

from test_agent.context import MAX_FILE_BYTES, SKILL_NAME, AgentError, AnalysisContext
from test_agent.planner import run_planner

ROOT = Path(__file__).resolve().parents[1]


def tool_calls(*calls):
    return {
        "role": "model",
        "parts": [
            {
                "functionCall": {"name": name, "args": args},
                "thoughtSignature": "opaque-signature",
            }
            for name, args in calls
        ],
    }


def valid_plan():
    return {
        "decision": "add_tests",
        "summary": "Check the module's default retention.",
        "test_cases": [
            {
                "name": "default_retention",
                "category": "default",
                "basis": "implemented_behavior",
                "purpose": "Catch a regression in the default.",
                "expected": "Retention defaults to 30.",
                "evidence": [{"file": "modules/demo/main.tf", "line": 2}],
            }
        ],
        "findings": [],
        "limitations": ["No Terraform tests have been run."],
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.usage = {}

    def generate(self, instructions, contents, tools):
        self.requests.append(
            copy.deepcopy(
                {"instructions": instructions, "contents": contents, "tools": tools}
            )
        )
        return next(self.responses)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name)
        self.module_dir = self.source / "modules" / "demo"
        self.module_dir.mkdir(parents=True)
        self.source_file = self.module_dir / "main.tf"
        self.source_file.write_text(
            'variable "retention" {\n  default = 30\n}\n', encoding="utf-8"
        )

    def context(self, module="demo"):
        return AnalysisContext(self.source, ROOT, module)

    def client(self, plan=None):
        return FakeClient(
            [
                tool_calls(("load_skill", {"name": SKILL_NAME})),
                tool_calls(("read_module", {"module": "demo"})),
                tool_calls(
                    ("submit_test_plan", valid_plan() if plan is None else plan)
                ),
                tool_calls(
                    ("submit_test_plan", valid_plan() if plan is None else plan)
                ),
            ]
        )

    def test_real_tool_sequence_produces_validated_plan_and_preserves_signatures(self):
        context, client = self.context(), self.client()
        before = self.source_file.read_bytes()
        report = run_planner(context, client)
        self.assertEqual(
            report["tool_calls"], ["load_skill", "read_module", "submit_test_plan"]
        )
        self.assertFalse(report["tests_executed"])
        self.assertEqual(report["mode"], "analysis_only")
        self.assertEqual(report["existing_test_files"], [])
        self.assertEqual(before, self.source_file.read_bytes())
        self.assertEqual(
            client.requests[1]["contents"][1]["parts"][0]["thoughtSignature"],
            "opaque-signature",
        )
        allowed = [
            [tool["name"] for tool in request["tools"]] for request in client.requests
        ]
        self.assertEqual(
            allowed,
            [["load_skill", "read_module"], ["read_module"], ["submit_test_plan"]],
        )
        delivered_skill = client.requests[1]["contents"][-1]["parts"][0][
            "functionResponse"
        ]["response"]
        self.assertEqual(delivered_skill["instructions"], context.skill)
        self.assertEqual(len(report["provenance"]["skill"]["sha256"]), 64)

    def test_discovery_tools_can_be_called_in_one_round(self):
        client = FakeClient(
            [
                tool_calls(
                    ("read_module", {"module": "demo"}),
                    ("load_skill", {"name": SKILL_NAME}),
                ),
                tool_calls(("submit_test_plan", valid_plan())),
            ]
        )
        self.assertEqual(
            run_planner(self.context(), client)["plan"]["decision"], "add_tests"
        )
        self.assertEqual(len(client.requests), 2)

    def test_unknown_premature_or_repeated_tools_are_rejected(self):
        for calls in [
            [("run_shell", {"command": "echo unsafe"})],
            [("submit_test_plan", valid_plan())],
            [("read_module", {"module": "demo"}), ("read_module", {"module": "demo"})],
        ]:
            with self.subTest(calls=calls), self.assertRaises(AgentError):
                run_planner(self.context(), FakeClient([tool_calls(*calls)]))

    def test_other_modules_skills_and_extra_arguments_are_rejected(self):
        for name, args in [
            ("read_module", {"module": "../private"}),
            ("read_module", {"module": "demo", "path": ".env"}),
            ("load_skill", {"name": "untrusted-skill"}),
        ]:
            with self.subTest(name=name, args=args), self.assertRaises(AgentError):
                run_planner(self.context(), FakeClient([tool_calls((name, args))]))

    def test_missing_or_hallucinated_evidence_is_rejected(self):
        for evidence in [
            {"file": ".env", "line": 1},
            {"file": "modules/demo/main.tf", "line": 999},
            {"file": "modules/demo/main.tf", "line": True},
        ]:
            plan = valid_plan()
            plan["test_cases"][0]["evidence"] = [evidence]
            with self.subTest(evidence=evidence), self.assertRaises(AgentError):
                run_planner(self.context(), self.client(plan))

    def test_model_cannot_add_a_fake_test_execution_result(self):
        plan = valid_plan()
        plan["tests_executed"] = True
        with self.assertRaises(AgentError):
            run_planner(self.context(), self.client(plan))

    def test_no_change_decision_requires_an_empty_case_list(self):
        plan = valid_plan()
        plan["decision"] = "no_change"
        with self.assertRaises(AgentError):
            run_planner(self.context(), self.client(plan))
        plan["test_cases"] = []
        plan["summary"] = "No further cases proposed based on the supplied source."
        self.assertEqual(
            run_planner(self.context(), self.client(plan))["plan"]["decision"],
            "no_change",
        )

    def test_one_structural_correction_keeps_validation_and_request_bound(self):
        bad = valid_plan()
        bad["decision"] = "no_change"
        good = {**bad, "test_cases": []}
        client = FakeClient(
            [
                tool_calls(("load_skill", {"name": SKILL_NAME})),
                tool_calls(("read_module", {"module": "demo"})),
                tool_calls(("submit_test_plan", bad)),
                tool_calls(("submit_test_plan", good)),
            ]
        )
        report = run_planner(self.context(), client)
        self.assertEqual(report["plan"]["decision"], "no_change")
        self.assertEqual(len(client.requests), 4)
        feedback = client.requests[-1]["contents"][-1]["parts"][0]["functionResponse"][
            "response"
        ]
        self.assertFalse(feedback["accepted"])
        self.assertNotIn("input", feedback["errors"][0])

    def test_module_argument_cannot_be_a_path(self):
        for module in ("../demo", "/etc", "C:\\secrets", "demo/main.tf"):
            with self.subTest(module=module), self.assertRaises(AgentError):
                self.context(module)

    def test_snapshot_excludes_secrets_and_is_stable_after_reading(self):
        (self.module_dir / ".env").write_text("private-value", encoding="utf-8")
        (self.module_dir / "terraform.tfstate").write_text(
            "private-value", encoding="utf-8"
        )
        context = self.context()
        manifest = context.provenance()
        self.source_file.write_text("changed after snapshot", encoding="utf-8")
        self.assertNotIn("private-value", str(context.module_data()))
        self.assertIn("default = 30", str(context.module_data()))
        self.assertEqual(manifest, context.provenance())

    def test_policies_are_loaded_from_the_trusted_root(self):
        (self.source / "agent").mkdir()
        (self.source / "agent" / "instructions.md").write_text(
            "untrusted-instructions", encoding="utf-8"
        )
        self.assertNotIn("untrusted-instructions", self.context().instructions)

    def test_oversized_source_is_rejected_instead_of_truncated(self):
        self.source_file.write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
        with self.assertRaises(AgentError):
            self.context()

    def test_link_to_an_outside_file_is_rejected(self):
        outside = self.source / "private.txt"
        outside.write_text("private-value", encoding="utf-8")
        linked = self.module_dir / "linked.tf"
        try:
            linked.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this Windows account.")
        with self.assertRaises(AgentError):
            self.context()


if __name__ == "__main__":
    unittest.main()
