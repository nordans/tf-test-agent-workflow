"""Offline validation snapshots never include executable scripts or test programs."""

import json
import subprocess
from unittest.mock import patch

import pytest

from test_agent.check_modules import snapshot, validate_repository
from test_agent.context import AgentError


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "repo"
    module = root / "modules/ai-search"
    module.mkdir(parents=True)
    (root / "stacks").mkdir()
    (module / "main.tf").write_text(
        'terraform { required_providers { azurerm = { source = "hashicorp/azurerm", version = "~> 5.0" } } }'
    )
    (module / "scripts").mkdir()
    (module / "scripts/external.sh").write_text("do-not-execute")
    (module / "tests").mkdir()
    (module / "tests/unsafe.tftest.hcl").write_text('run "apply" {}')
    return root


def test_snapshot_discovers_new_module_and_copies_only_sources_and_matching_lock(
    source, tmp_path
):
    target = tmp_path / "snapshot"
    assert snapshot(source, target) == ["modules/ai-search"]
    paths = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    assert paths == {
        "modules/ai-search/main.tf",
        "modules/ai-search/.terraform.lock.hcl",
    }
    lock = (target / "modules/ai-search/.terraform.lock.hcl").read_text()
    assert "hashicorp/azurerm" in lock and "hashicorp/external" not in lock


def test_unapproved_provider_never_reaches_validation(source, tmp_path):
    path = source / "modules/ai-search/main.tf"
    path.write_text(path.read_text().replace("azurerm", "unapproved"))
    with pytest.raises(AgentError, match="approved provider"):
        snapshot(source, tmp_path / "snapshot")


@pytest.mark.parametrize("payload", [{"valid": True}, {"valid": False}])
def test_process_failure_cannot_be_overridden_by_json_and_container_is_cleaned(
    source, payload
):
    result = subprocess.CompletedProcess([], 1, json.dumps(payload).encode(), b"")
    with (
        patch("test_agent.check_modules.runtime_id", return_value="sha256:" + "a" * 64),
        patch("test_agent.check_modules.docker", return_value=result) as command,
    ):
        report = validate_repository(source)
    assert report["passed"] is False
    assert report["modules"][0]["valid"] is False
    assert command.call_args.args[0][:2] == ["rm", "--force"]
    arguments = command.call_args_list[0].args[0]
    assert "--network=none" in arguments and "--read-only" in arguments
    assert arguments[-2:] == ["/opt/check-module.sh", "modules/ai-search"]


def test_malformed_json_cannot_be_accepted_and_container_is_cleaned(source):
    result = subprocess.CompletedProcess([], 0, b"[]", b"")
    with (
        patch("test_agent.check_modules.runtime_id", return_value="sha256:" + "a" * 64),
        patch("test_agent.check_modules.docker", return_value=result) as command,
    ):
        with pytest.raises(AgentError, match="malformed"):
            validate_repository(source)
    assert command.call_args.args[0][:2] == ["rm", "--force"]
