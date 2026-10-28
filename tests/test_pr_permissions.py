"""Prevent accidental privilege or checkout regressions in the agent workflow."""

from pathlib import Path

import yaml

WORKFLOW = yaml.safe_load(
    (
        Path(__file__).resolve().parents[1] / ".github/workflows/pr-test-agent.yml"
    ).read_text()
)


def test_all_jobs_checkout_only_the_trusted_workflow_commit():
    for job in WORKFLOW["jobs"].values():
        checkouts = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        ]
        assert len(checkouts) == 1
        assert checkouts[0]["with"]["ref"] == "$" + "{{ github.sha }}"
        assert checkouts[0]["with"]["persist-credentials"] is False


def test_model_secret_and_write_permission_are_on_different_workers():
    assert WORKFLOW["permissions"] == {}
    for name, job in WORKFLOW["jobs"].items():
        for step in job["steps"]:
            if "GEMINI_API_KEY" in step.get("env", {}):
                assert name == "propose"
        permissions = job["permissions"]
        assert permissions.get("contents") == "read"
        if name == "publish":
            assert permissions["pull-requests"] == "write"
        else:
            assert "write" not in permissions.values()


def test_unscanned_context_is_never_uploaded_on_prepare_failure():
    steps = WORKFLOW["jobs"]["prepare"]["steps"]
    uploads = [
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
    assert uploads[0].get("if", "success()") == "success()"
    assert uploads[0]["with"]["path"] == "out/context.json"
