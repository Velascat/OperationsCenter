# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for kodo mapper: ExecutionRequest → KodoPreparedRun."""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.contracts.execution import ExecutionRequest
from operations_center.backends.kodo.mapper import check_support, map_request
from operations_center.backends.kodo.models import KodoPreparedRun


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request(**kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Fix all lint errors in src/",
        repo_key="api-service",
        clone_url="https://git.example.com/api-service.git",
        base_branch="main",
        task_branch="auto/lint-fix-abc",
        workspace_path=Path("/tmp/ws/api-service"),
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


# ---------------------------------------------------------------------------
# check_support
# ---------------------------------------------------------------------------

class TestCheckSupport:
    def test_minimal_valid_request_is_supported(self):
        check = check_support(_request())
        assert check.supported is True
        assert check.reason is None

    def test_empty_goal_text_not_supported(self):
        check = check_support(_request(goal_text="   "))
        assert check.supported is False
        assert "goal_text" in check.unsupported_fields

    def test_empty_repo_key_not_supported(self):
        check = check_support(_request(repo_key=""))
        assert check.supported is False
        assert "repo_key" in check.unsupported_fields

    def test_support_check_yes_factory(self):
        from operations_center.backends.kodo.models import SupportCheck
        check = SupportCheck.yes()
        assert check.supported is True

    def test_support_check_no_factory(self):
        from operations_center.backends.kodo.models import SupportCheck
        check = SupportCheck.no("missing field", fields=["goal_text"])
        assert check.supported is False
        assert check.unsupported_fields == ["goal_text"]


# ---------------------------------------------------------------------------
# map_request
# ---------------------------------------------------------------------------

class TestMapRequest:
    def test_basic_mapping(self):
        r = _request()
        prepared = map_request(r)
        assert isinstance(prepared, KodoPreparedRun)
        assert prepared.run_id == r.run_id
        assert prepared.goal_text == r.goal_text
        assert prepared.repo_path == Path(r.workspace_path)
        assert prepared.task_branch == r.task_branch

    def test_constraints_text_preserved(self):
        r = _request(constraints_text="Do not touch auth/")
        prepared = map_request(r)
        assert prepared.constraints_text == "Do not touch auth/"

    def test_no_constraints_text_is_none(self):
        r = _request()
        prepared = map_request(r)
        assert prepared.constraints_text is None

    def test_timeout_seconds_preserved(self):
        r = _request(timeout_seconds=600)
        prepared = map_request(r)
        assert prepared.timeout_seconds == 600

    def test_validation_commands_preserved(self):
        r = _request(validation_commands=["ruff check .", "pytest"])
        prepared = map_request(r)
        assert prepared.validation_commands == ["ruff check .", "pytest"]

    def test_default_goal_file_path_inside_workspace(self):
        r = _request()
        prepared = map_request(r)
        assert prepared.goal_file_path == Path("/tmp/ws/api-service/.kodo_goal.md")

    def test_explicit_goal_file_path_respected(self):
        r = _request(goal_file_path=Path("/tmp/ws/api-service/custom_goal.md"))
        prepared = map_request(r)
        assert prepared.goal_file_path == Path("/tmp/ws/api-service/custom_goal.md")

    def test_kodo_mode_default_is_goal(self):
        r = _request()
        prepared = map_request(r)
        assert prepared.kodo_mode == "goal"

    def test_kodo_mode_override(self):
        r = _request()
        prepared = map_request(r, kodo_mode="test")
        assert prepared.kodo_mode == "test"

    def test_unsupported_request_raises(self):
        r = _request(goal_text="")
        with pytest.raises(ValueError, match="not suitable for kodo"):
            map_request(r)

    def test_env_overrides_empty_by_default(self):
        r = _request()
        prepared = map_request(r)
        assert prepared.env_overrides == {}
