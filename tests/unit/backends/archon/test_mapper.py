"""Tests for backends/archon/mapper.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.backends.archon.mapper import check_support, map_request
from operations_center.backends.archon.models import SupportCheck
from operations_center.contracts.execution import ExecutionRequest


def _req(tmp_path: Path, **kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Refactor the login module",
        repo_key="auth-service",
        clone_url="https://git.example.com/auth.git",
        base_branch="main",
        task_branch="auto/refactor-login-abc",
        workspace_path=tmp_path / "repo",
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


# ---------------------------------------------------------------------------
# check_support
# ---------------------------------------------------------------------------


def test_valid_request_is_supported(tmp_path):
    check = check_support(_req(tmp_path))
    assert check.supported is True


def test_empty_goal_not_supported(tmp_path):
    check = check_support(_req(tmp_path, goal_text="   "))
    assert check.supported is False
    assert "goal_text" in check.unsupported_fields


def test_missing_repo_key_not_supported(tmp_path):
    check = check_support(_req(tmp_path, repo_key=""))
    assert check.supported is False
    assert "repo_key" in check.unsupported_fields


def test_missing_workspace_path_not_supported(tmp_path):
    check = check_support(_req(tmp_path, workspace_path=Path("")))
    assert check.supported is False
    assert "workspace_path" in check.unsupported_fields


def test_multiple_missing_fields_reported(tmp_path):
    check = check_support(_req(tmp_path, goal_text="", repo_key=""))
    assert not check.supported
    assert "goal_text" in check.unsupported_fields
    assert "repo_key" in check.unsupported_fields


def test_support_check_reason_is_set(tmp_path):
    check = check_support(_req(tmp_path, goal_text=""))
    assert check.reason is not None
    assert len(check.reason) > 0


def test_support_check_yes_factory():
    check = SupportCheck.yes()
    assert check.supported is True
    assert check.reason is None


def test_support_check_no_factory():
    check = SupportCheck.no("missing goal_text", fields=["goal_text"])
    assert check.supported is False
    assert "goal_text" in check.unsupported_fields


# ---------------------------------------------------------------------------
# map_request — successful mapping
# ---------------------------------------------------------------------------


def test_map_request_returns_workflow_config(tmp_path):
    from operations_center.backends.archon.models import ArchonWorkflowConfig
    config = map_request(_req(tmp_path))
    assert isinstance(config, ArchonWorkflowConfig)


def test_run_id_preserved(tmp_path):
    req = _req(tmp_path)
    config = map_request(req)
    assert config.run_id == req.run_id


def test_goal_text_preserved(tmp_path):
    req = _req(tmp_path, goal_text="Refactor the payment processor")
    config = map_request(req)
    assert config.goal_text == "Refactor the payment processor"


def test_constraints_text_preserved(tmp_path):
    req = _req(tmp_path, constraints_text="Only touch src/payments/")
    config = map_request(req)
    assert config.constraints_text == "Only touch src/payments/"


def test_repo_path_is_workspace_path(tmp_path):
    ws = tmp_path / "repo"
    req = _req(tmp_path, workspace_path=ws)
    config = map_request(req)
    assert config.repo_path == ws


def test_task_branch_preserved(tmp_path):
    req = _req(tmp_path, task_branch="auto/feat-xyz")
    config = map_request(req)
    assert config.task_branch == "auto/feat-xyz"


def test_timeout_preserved(tmp_path):
    req = _req(tmp_path, timeout_seconds=600)
    config = map_request(req)
    assert config.timeout_seconds == 600


def test_validation_commands_preserved(tmp_path):
    req = _req(tmp_path, validation_commands=["ruff check src/", "mypy src/"])
    config = map_request(req)
    assert config.validation_commands == ["ruff check src/", "mypy src/"]


def test_default_workflow_type_is_goal(tmp_path):
    config = map_request(_req(tmp_path))
    assert config.workflow_type == "goal"


def test_explicit_workflow_type_preserved(tmp_path):
    config = map_request(_req(tmp_path), workflow_type="fix_pr")
    assert config.workflow_type == "fix_pr"


def test_metadata_contains_proposal_id(tmp_path):
    req = _req(tmp_path)
    config = map_request(req)
    assert config.metadata["proposal_id"] == req.proposal_id


def test_metadata_contains_task_branch(tmp_path):
    req = _req(tmp_path, task_branch="auto/feat-abc")
    config = map_request(req)
    assert config.metadata["task_branch"] == "auto/feat-abc"


def test_allowed_paths_in_metadata_when_set(tmp_path):
    req = _req(tmp_path, allowed_paths=["src/**", "tests/**"])
    config = map_request(req)
    assert "allowed_paths" in config.metadata


def test_none_constraints_text_maps_to_none(tmp_path):
    req = _req(tmp_path, constraints_text=None)
    config = map_request(req)
    assert config.constraints_text is None


# ---------------------------------------------------------------------------
# map_request — validation error propagation
# ---------------------------------------------------------------------------


def test_map_unsupported_request_raises(tmp_path):
    req = _req(tmp_path, goal_text="")
    with pytest.raises(ValueError, match="not suitable for Archon"):
        map_request(req)
