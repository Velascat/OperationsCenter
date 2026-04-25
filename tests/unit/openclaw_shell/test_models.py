"""Tests for openclaw_shell/models.py — shell model construction and defaults."""

from __future__ import annotations

import pytest

from operations_center.openclaw_shell.models import (
    OperatorContext,
    ShellActionResult,
    ShellInspectionResult,
    ShellRunHandle,
    ShellStatusSummary,
)


# ---------------------------------------------------------------------------
# OperatorContext
# ---------------------------------------------------------------------------


def test_operator_context_minimal():
    ctx = OperatorContext(goal_text="Fix lint errors", repo_key="svc")
    assert ctx.goal_text == "Fix lint errors"
    assert ctx.repo_key == "svc"


def test_operator_context_defaults():
    ctx = OperatorContext(goal_text="Fix lint errors", repo_key="svc")
    assert ctx.task_type == "goal"
    assert ctx.execution_mode == "goal"
    assert ctx.risk_level == "low"
    assert ctx.priority == "normal"
    assert ctx.base_branch == "main"
    assert ctx.labels == []
    assert ctx.allowed_paths == []
    assert ctx.timeout_seconds == 300
    assert ctx.shell_flags == {}
    assert ctx.constraints_text is None
    assert ctx.clone_url == ""


def test_operator_context_with_labels():
    ctx = OperatorContext(
        goal_text="Refactor auth",
        repo_key="svc",
        labels=["local_only", "priority:high"],
    )
    assert "local_only" in ctx.labels


def test_operator_context_constraints_text():
    ctx = OperatorContext(
        goal_text="Fix tests",
        repo_key="svc",
        constraints_text="Only touch tests/ dir",
    )
    assert ctx.constraints_text == "Only touch tests/ dir"


def test_operator_context_shell_flags():
    ctx = OperatorContext(
        goal_text="do thing",
        repo_key="svc",
        shell_flags={"dry_run": True, "verbose": False},
    )
    assert ctx.shell_flags["dry_run"] is True


# ---------------------------------------------------------------------------
# ShellRunHandle
# ---------------------------------------------------------------------------


def _handle(**kw) -> ShellRunHandle:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        selected_lane="claude_cli",
        selected_backend="kodo",
        routing_confidence=0.9,
        status="planned",
        summary="proposal=prop-1 lane=claude_cli backend=kodo rule=medium_implementation",
    )
    defaults.update(kw)
    return ShellRunHandle(**defaults)


def test_shell_run_handle_construction():
    h = _handle()
    assert h.proposal_id == "prop-1"
    assert h.decision_id == "dec-1"
    assert h.selected_lane == "claude_cli"
    assert h.selected_backend == "kodo"


def test_shell_run_handle_defaults():
    h = _handle()
    assert h.status == "planned"
    assert h.policy_rule is None or isinstance(h.policy_rule, str)
    assert h.handle_id is not None


def test_shell_run_handle_is_frozen():
    h = _handle()
    with pytest.raises(Exception):
        h.status = "running"


def test_shell_run_handle_unique_handle_ids():
    h1 = _handle()
    h2 = _handle()
    assert h1.handle_id != h2.handle_id


def test_shell_run_handle_policy_rule_optional():
    h = _handle(policy_rule="medium_implementation")
    assert h.policy_rule == "medium_implementation"
    h2 = _handle(policy_rule=None)
    assert h2.policy_rule is None


def test_shell_run_handle_confidence_range():
    with pytest.raises(Exception):
        _handle(routing_confidence=1.5)


# ---------------------------------------------------------------------------
# ShellStatusSummary
# ---------------------------------------------------------------------------


def _status_summary(**kw) -> ShellStatusSummary:
    defaults = dict(
        run_id="run-1",
        proposal_id="prop-1",
        decision_id="dec-1",
        status="success",
        success=True,
        headline="SUCCESS | kodo @ claude_cli | run=run-1abc",
        summary="Run run-1abc; changed 3 files",
    )
    defaults.update(kw)
    return ShellStatusSummary(**defaults)


def test_status_summary_construction():
    s = _status_summary()
    assert s.run_id == "run-1"
    assert s.status == "success"
    assert s.success is True


def test_status_summary_defaults():
    s = _status_summary()
    assert s.selected_lane is None
    assert s.selected_backend is None
    assert s.changed_files_status == "unknown"
    assert s.validation_status == "skipped"
    assert s.artifact_count == 0
    assert s.recorded_at is None


def test_status_summary_is_frozen():
    s = _status_summary()
    with pytest.raises(Exception):
        s.status = "failed"


def test_status_summary_with_lane_backend():
    s = _status_summary(selected_lane="claude_cli", selected_backend="kodo")
    assert s.selected_lane == "claude_cli"
    assert s.selected_backend == "kodo"


def test_status_summary_failure():
    s = _status_summary(
        status="failed",
        success=False,
        headline="FAILED | kodo @ claude_cli | run=run-1",
        summary="Run run-1; failed: workflow step aborted",
    )
    assert s.success is False
    assert "FAILED" in s.headline


# ---------------------------------------------------------------------------
# ShellInspectionResult
# ---------------------------------------------------------------------------


def _inspection(**kw) -> ShellInspectionResult:
    defaults = dict(
        run_id="run-1",
        proposal_id="prop-1",
        decision_id="dec-1",
        status="success",
        headline="SUCCESS | kodo @ claude_cli | run=run-1",
        summary="Run run-1; changed 3 files; validation=passed",
    )
    defaults.update(kw)
    return ShellInspectionResult(**defaults)


def test_inspection_construction():
    r = _inspection()
    assert r.run_id == "run-1"
    assert r.status == "success"


def test_inspection_defaults():
    r = _inspection()
    assert r.warnings == []
    assert r.artifact_count == 0
    assert r.primary_artifact_count == 0
    assert r.changed_files_status == "unknown"
    assert r.validation_status == "skipped"
    assert r.backend_detail_count == 0
    assert r.selected_lane is None
    assert r.trace_id is None
    assert r.record_id is None


def test_inspection_is_frozen():
    r = _inspection()
    with pytest.raises(Exception):
        r.status = "failed"


def test_inspection_with_warnings():
    r = _inspection(warnings=["no primary artifacts", "validation skipped"])
    assert len(r.warnings) == 2


# ---------------------------------------------------------------------------
# ShellActionResult
# ---------------------------------------------------------------------------


def test_action_result_success():
    r = ShellActionResult(action="trigger", success=True, message="ok")
    assert r.success is True
    assert r.action == "trigger"


def test_action_result_failure():
    r = ShellActionResult(
        action="trigger",
        success=False,
        message="routing failed",
        detail="ValueError",
    )
    assert r.success is False
    assert r.detail == "ValueError"


def test_action_result_is_frozen():
    r = ShellActionResult(action="trigger", success=True, message="ok")
    with pytest.raises(Exception):
        r.success = False
