"""Tests for backends/archon/normalize.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.backends.archon.models import ArchonArtifactCapture, ArchonRunCapture
from operations_center.backends.archon.normalize import normalize
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _capture(
    outcome: str = "success",
    exit_code: int = 0,
    output_text: str = "archon: done",
    error_text: str = "",
    timeout_hit: bool = False,
    artifacts: list[ArchonArtifactCapture] | None = None,
    workflow_events: list[dict] | None = None,
) -> ArchonRunCapture:
    return ArchonRunCapture(
        run_id="run-001",
        outcome=outcome,
        exit_code=exit_code,
        output_text=output_text,
        error_text=error_text,
        workflow_events=workflow_events or [],
        artifacts=artifacts or [],
        started_at=_now(),
        finished_at=_now(),
        duration_ms=500,
        timeout_hit=timeout_hit,
    )


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


def test_success_outcome_gives_success_status():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1")
    assert result.status == ExecutionStatus.SUCCESS
    assert result.success is True


def test_success_failure_fields_are_none():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1")
    assert result.failure_category is None
    assert result.failure_reason is None


def test_run_id_preserved():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1")
    assert result.run_id == "run-001"


def test_proposal_id_preserved():
    result = normalize(_capture(), proposal_id="prop-42", decision_id="d1")
    assert result.proposal_id == "prop-42"


def test_decision_id_preserved():
    result = normalize(_capture(), proposal_id="p1", decision_id="dec-99")
    assert result.decision_id == "dec-99"


def test_branch_name_preserved():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1", branch_name="auto/refactor")
    assert result.branch_name == "auto/refactor"


def test_branch_pushed_is_false():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1")
    assert result.branch_pushed is False


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


def test_failure_outcome_gives_failed_status():
    c = _capture(outcome="failure", exit_code=1, error_text="workflow step failed")
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.status == ExecutionStatus.FAILED
    assert result.success is False


def test_partial_outcome_gives_failed_status():
    c = _capture(outcome="partial", exit_code=1)
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.status == ExecutionStatus.FAILED


def test_failure_category_set_on_failure():
    c = _capture(outcome="failure", exit_code=1)
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.failure_category is not None


def test_failure_reason_set_on_failure():
    c = _capture(outcome="failure", error_text="workflow aborted")
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.failure_reason is not None
    assert len(result.failure_reason) > 0


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_timeout_outcome_gives_timeout_status():
    c = _capture(outcome="timeout", timeout_hit=True)
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.status == ExecutionStatus.TIMEOUT


def test_timeout_hit_flag_gives_timeout_status():
    c = _capture(outcome="failure", timeout_hit=True)
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.status == ExecutionStatus.TIMEOUT


def test_timeout_failure_category():
    c = _capture(outcome="timeout", timeout_hit=True)
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.failure_category == FailureReasonCategory.TIMEOUT


# ---------------------------------------------------------------------------
# No changes
# ---------------------------------------------------------------------------


def test_no_changes_category():
    c = _capture(outcome="failure", error_text="no changes detected")
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.failure_category == FailureReasonCategory.NO_CHANGES


# ---------------------------------------------------------------------------
# Validation summary
# ---------------------------------------------------------------------------


def test_validation_skipped_by_default():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1")
    assert result.validation.status == ValidationStatus.SKIPPED


def test_validation_passed_when_provided():
    result = normalize(
        _capture(),
        proposal_id="p1",
        decision_id="d1",
        validation_ran=True,
        validation_passed=True,
        validation_duration_ms=500,
    )
    assert result.validation.status == ValidationStatus.PASSED
    assert result.validation.commands_passed == 1


def test_validation_failed_when_provided():
    result = normalize(
        _capture(),
        proposal_id="p1",
        decision_id="d1",
        validation_ran=True,
        validation_passed=False,
        validation_excerpt="ruff: 3 errors",
    )
    assert result.validation.status == ValidationStatus.FAILED
    assert result.validation.failure_excerpt == "ruff: 3 errors"


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def test_log_excerpt_artifact_mapped():
    c = _capture(artifacts=[
        ArchonArtifactCapture(label="archon log", content="archon: done", artifact_type="log_excerpt"),
    ])
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert len(result.artifacts) == 1
    assert result.artifacts[0].artifact_type == ArtifactType.LOG_EXCERPT


def test_unknown_artifact_type_defaults_to_log_excerpt():
    c = _capture(artifacts=[
        ArchonArtifactCapture(label="mystery", content="data", artifact_type="unknown_type"),
    ])
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.artifacts[0].artifact_type == ArtifactType.LOG_EXCERPT


def test_diff_artifact_type_preserved():
    c = _capture(artifacts=[
        ArchonArtifactCapture(label="patch", content="--- a/...", artifact_type="diff"),
    ])
    result = normalize(c, proposal_id="p1", decision_id="d1")
    assert result.artifacts[0].artifact_type == ArtifactType.DIFF


# ---------------------------------------------------------------------------
# Workflow events NOT propagated into ExecutionResult
# ---------------------------------------------------------------------------


def test_workflow_events_not_in_execution_result():
    events = [{"step": "plan"}, {"step": "execute"}]
    c = _capture(workflow_events=events)
    result = normalize(c, proposal_id="p1", decision_id="d1")
    # ExecutionResult has no workflow_events field
    assert not hasattr(result, "workflow_events")
    # Artifacts and other fields are unrelated to events
    assert result.success is True


# ---------------------------------------------------------------------------
# Changed files — no workspace
# ---------------------------------------------------------------------------


def test_no_changed_files_without_workspace():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1", workspace_path=None)
    assert result.changed_files == []
    assert result.diff_stat_excerpt is None
    assert result.changed_files_source == "unknown"
    assert result.changed_files_confidence == 0.0
