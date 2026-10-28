"""Policy checks must fail before Terraform can interpret disallowed constructs."""

import unittest
from pathlib import Path

from test_agent.context import AgentError, AnalysisContext
from test_agent.hcl_policy import MAX_HCL_BYTES, validate_proposal, validate_sources

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests/fixtures/log_analytics_valid.tftest.hcl").read_text()


class HclPolicyTests(unittest.TestCase):
    def test_supported_proposal_and_source_are_accepted(self):
        self.assertEqual(
            validate_proposal(FIXTURE).runs, ("defaults", "overrides", "output_id")
        )
        validate_sources(AnalysisContext(ROOT, ROOT, "log-analytics-workspace").files)

    def test_execution_and_external_inputs_are_rejected(self):
        examples = [
            FIXTURE.replace("command = plan", "command = apply", 1),
            FIXTURE.replace("command = plan", "", 1),
            FIXTURE + '\nprovider "azurerm" { features {} }',
            FIXTURE.replace(
                "override_during = plan",
                'override_during = plan\nsource = "../mocks"',
                1,
            ),
            FIXTURE.replace(
                "command = plan", 'command = plan\nmodule { source = "../other" }', 1
            ),
            FIXTURE.replace(
                "length(azurerm_log_analytics_workspace.this.tags)",
                'length(file("/proc/self/environ"))',
                1,
            ),
            FIXTURE
            + "\noverride_resource { target = azurerm_log_analytics_workspace.this }",
            FIXTURE.replace(
                'id = "/subscriptions/',
                'retention_in_days = 90\nid = "/subscriptions/',
                1,
            ),
        ]
        for index, example in enumerate(examples):
            with self.subTest(index=index), self.assertRaises(AgentError):
                validate_proposal(example)

    def test_invalid_empty_or_oversized_proposals_are_rejected(self):
        for example in (
            "",
            "invalid {{{",
            "x" * (MAX_HCL_BYTES + 1),
            'run "x" { command = plan }',
        ):
            with self.subTest(length=len(example)), self.assertRaises(AgentError):
                validate_proposal(example)

    def test_source_cannot_add_a_backend_provider_or_external_program(self):
        original = AnalysisContext(ROOT, ROOT, "log-analytics-workspace").files
        for extra in (
            'data "external" "bad" { program = ["sh", "script.sh"] }',
            'provider "azurerm" { features {} }',
            'module "bad" { source = "https://example.invalid/module.zip" }',
        ):
            files = {**original, "modules/log-analytics-workspace/extra.tf": extra}
            with self.subTest(extra=extra), self.assertRaises(AgentError):
                validate_sources(files)
        files = dict(original)
        path = "modules/log-analytics-workspace/versions.tf"
        files[path] = files[path].replace(
            "terraform {", 'terraform {\nbackend "http" {}', 1
        )
        with self.assertRaises(AgentError):
            validate_sources(files)

    def test_source_requires_the_reviewed_provider_source_and_constraint(self):
        original = AnalysisContext(ROOT, ROOT, "log-analytics-workspace").files
        path = "modules/log-analytics-workspace/versions.tf"
        for before, after in (
            (">= 4.0, < 6.0", ">= 4.0"),
            ("hashicorp/azurerm", "example/azurerm"),
        ):
            files = {**original, path: original[path].replace(before, after)}
            with self.subTest(after=after), self.assertRaises(AgentError):
                validate_sources(files)
