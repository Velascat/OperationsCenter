# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 2 — RxP wire pinning for archon's invoker.

Archon is an out-of-process service (not a subprocess), so the
ExecutorRuntime extraction (Phase 3) doesn't apply. What does apply
is documenting what the invoker is dispatching in RxP terms so
future runner abstraction (HTTP/manual) doesn't require a contract
change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends.archon.invoke import (
    ArchonRunResult,
    _build_invocation,
    _build_runtime_result,
)
from operations_center.backends.archon.models import ArchonWorkflowConfig


def _config(tmp_path: Path, **overrides) -> ArchonWorkflowConfig:
    defaults = dict(
        run_id="run-arc-1",
        goal_text="Migrate API",
        constraints_text=None,
        repo_path=tmp_path / "repo",
        task_branch="auto/arc",
        workflow_type="goal",
        timeout_seconds=600,
        validation_commands=[],
        metadata={"proposal_id": "prop-1", "decision_id": "dec-1"},
        env_overrides={"FOO": "bar"},
    )
    defaults.update(overrides)
    return ArchonWorkflowConfig(**defaults)


class TestBuildInvocation:
    def test_invocation_is_rxp_runtime_invocation(self, tmp_path):
        invocation = _build_invocation(_config(tmp_path))
        assert isinstance(invocation, RuntimeInvocation)

    def test_runtime_kind_is_manual(self, tmp_path):
        """archon is dispatched to an out-of-process service, not a subprocess."""
        invocation = _build_invocation(_config(tmp_path))
        assert invocation.runtime_kind == "manual"

    def test_carries_runtime_metadata(self, tmp_path):
        config = _config(tmp_path)
        invocation = _build_invocation(config)
        assert invocation.invocation_id == "run-arc-1"
        assert invocation.runtime_name == "archon"
        assert invocation.working_directory == str(config.repo_path)
        assert invocation.timeout_seconds == 600
        assert invocation.environment == {"FOO": "bar"}

    def test_command_is_descriptive(self, tmp_path):
        """The command captures workflow type + run id for observability."""
        invocation = _build_invocation(_config(tmp_path, workflow_type="fix_pr"))
        assert invocation.command[0] == "archon-workflow"
        assert "--workflow-type" in invocation.command
        assert "fix_pr" in invocation.command
        assert "--run-id" in invocation.command
        assert "run-arc-1" in invocation.command

    def test_metadata_carries_workflow_type_and_branch(self, tmp_path):
        invocation = _build_invocation(
            _config(tmp_path, workflow_type="improve", task_branch="my-branch"),
        )
        assert invocation.metadata["workflow_type"] == "improve"
        assert invocation.metadata["task_branch"] == "my-branch"

    def test_metadata_includes_passthrough_string_fields(self, tmp_path):
        invocation = _build_invocation(_config(tmp_path))
        assert invocation.metadata["proposal_id"] == "prop-1"
        assert invocation.metadata["decision_id"] == "dec-1"

    def test_zero_timeout_becomes_none(self, tmp_path):
        invocation = _build_invocation(_config(tmp_path, timeout_seconds=0))
        assert invocation.timeout_seconds is None


class TestBuildRuntimeResult:
    def _now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def test_success_outcome_maps_to_succeeded(self, tmp_path):
        invocation = _build_invocation(_config(tmp_path))
        raw = ArchonRunResult(outcome="success", exit_code=0)
        result = _build_runtime_result(
            invocation=invocation, raw=raw, timeout_hit=False,
            started_at=self._now(), finished_at=self._now(),
        )
        assert isinstance(result, RuntimeResult)
        assert result.status == "succeeded"
        assert result.exit_code == 0
        assert result.runtime_kind == "manual"

    def test_failure_outcome_maps_to_failed(self, tmp_path):
        invocation = _build_invocation(_config(tmp_path))
        raw = ArchonRunResult(outcome="failure", exit_code=1, error_text="boom")
        result = _build_runtime_result(
            invocation=invocation, raw=raw, timeout_hit=False,
            started_at=self._now(), finished_at=self._now(),
        )
        assert result.status == "failed"
        assert result.error_summary == "boom"

    def test_timeout_hit_overrides_outcome(self, tmp_path):
        invocation = _build_invocation(_config(tmp_path))
        raw = ArchonRunResult(outcome="failure", exit_code=124)
        result = _build_runtime_result(
            invocation=invocation, raw=raw, timeout_hit=True,
            started_at=self._now(), finished_at=self._now(),
        )
        assert result.status == "timed_out"

    def test_partial_outcome_maps_to_succeeded(self, tmp_path):
        """RxP has no 'partial' status; archon's partial maps to succeeded
        and downstream normalization decides whether it counts as failure.
        """
        invocation = _build_invocation(_config(tmp_path))
        raw = ArchonRunResult(outcome="partial", exit_code=0)
        result = _build_runtime_result(
            invocation=invocation, raw=raw, timeout_hit=False,
            started_at=self._now(), finished_at=self._now(),
        )
        assert result.status == "succeeded"
