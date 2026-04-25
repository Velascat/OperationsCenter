"""Tests for backends/openclaw/normalize.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.backends.openclaw.models import OpenClawArtifactCapture, OpenClawRunCapture
from operations_center.backends.openclaw.normalize import normalize
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
    output_text: str = "openclaw: done",
    error_text: str = "",
    timeout_hit: bool = False,
    artifacts: list[OpenClawArtifactCapture] | None = None,
    events: list[dict] | None = None,
    reported_changed_files: list[dict] | None = None,
    changed_files_source: str = "unknown",
) -> OpenClawRunCapture:
    return OpenClawRunCapture(
        run_id="run-001",
        outcome=outcome,
        exit_code=exit_code,
        output_text=output_text,
        error_text=error_text,
        events=events or [],
        artifacts=artifacts or [],
        reported_changed_files=reported_changed_files or [],
        changed_files_source=changed_files_source,
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
    result = normalize(
        _capture(), proposal_id="p1", decision_id="d1",
        branch_name="auto/fix-abc"
    )
    assert result.branch_name == "auto/fix-abc"


def test_branch_pushed_always_false():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1")
    assert result.branch_pushed is False


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


def test_failure_outcome_gives_failed_status():
    result = normalize(
        _capture(outcome="failure", exit_code=1),
        proposal_id="p1", decision_id="d1",
    )
    assert result.status == ExecutionStatus.FAILED
    assert result.success is False


def test_timeout_gives_timeout_status():
    result = normalize(
        _capture(outcome="timeout", timeout_hit=True),
        proposal_id="p1", decision_id="d1",
    )
    assert result.status == ExecutionStatus.TIMEOUT
    assert result.success is False


def test_timeout_hit_flag_also_gives_timeout():
    result = normalize(
        _capture(outcome="failure", timeout_hit=True),
        proposal_id="p1", decision_id="d1",
    )
    assert result.status == ExecutionStatus.TIMEOUT


def test_failure_category_set():
    result = normalize(
        _capture(outcome="failure", error_text="tool call failed"),
        proposal_id="p1", decision_id="d1",
    )
    assert result.failure_category is not None


def test_failure_reason_set():
    result = normalize(
        _capture(outcome="failure", error_text="tool call failed"),
        proposal_id="p1", decision_id="d1",
    )
    assert result.failure_reason is not None
    assert len(result.failure_reason) > 0


def test_no_changes_failure_category():
    result = normalize(
        _capture(outcome="failure", output_text="no changes detected"),
        proposal_id="p1", decision_id="d1",
    )
    assert result.failure_category == FailureReasonCategory.NO_CHANGES


def test_timeout_failure_category():
    result = normalize(
        _capture(outcome="timeout", timeout_hit=True),
        proposal_id="p1", decision_id="d1",
    )
    assert result.failure_category == FailureReasonCategory.TIMEOUT


# ---------------------------------------------------------------------------
# Changed-file evidence — git_diff path
# ---------------------------------------------------------------------------


def test_changed_files_from_git_diff(tmp_path):
    (tmp_path / "repo").mkdir()
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git"
    ) as mock_git:
        mock_git.return_value = [
            __import__(
                "operations_center.contracts.common",
                fromlist=["ChangedFileRef"],
            ).ChangedFileRef(path="src/main.py", change_type="modified")
        ]
        capture = _capture()
        result = normalize(capture, proposal_id="p1", decision_id="d1",
                           workspace_path=tmp_path / "repo")

    assert len(result.changed_files) == 1
    assert result.changed_files[0].path == "src/main.py"


def test_changed_files_source_set_to_git_diff(tmp_path):
    (tmp_path / "repo").mkdir()
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git"
    ) as mock_git:
        from operations_center.contracts.common import ChangedFileRef
        mock_git.return_value = [ChangedFileRef(path="src/a.py", change_type="modified")]
        capture = _capture()
        normalize(capture, proposal_id="p1", decision_id="d1",
                  workspace_path=tmp_path / "repo")

    assert capture.changed_files_source == "git_diff"


def test_result_preserves_git_diff_provenance(tmp_path):
    (tmp_path / "repo").mkdir()
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git"
    ) as mock_git:
        from operations_center.contracts.common import ChangedFileRef
        mock_git.return_value = [ChangedFileRef(path="src/a.py", change_type="modified")]
        capture = _capture()
        result = normalize(capture, proposal_id="p1", decision_id="d1", workspace_path=tmp_path / "repo")

    assert result.changed_files_source == "git_diff"
    assert result.changed_files_confidence == 1.0


# ---------------------------------------------------------------------------
# Changed-file evidence — event_stream fallback
# ---------------------------------------------------------------------------


def test_changed_files_from_event_stream_when_git_unavailable():
    files = [{"path": "src/main.py", "change_type": "modified"}]
    capture = _capture(reported_changed_files=files, changed_files_source="event_stream")
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git",
        return_value=None,
    ):
        result = normalize(capture, proposal_id="p1", decision_id="d1",
                           workspace_path=Path("/nonexistent/workspace"))

    assert len(result.changed_files) == 1
    assert result.changed_files[0].path == "src/main.py"


def test_changed_files_source_event_stream_after_git_fallback():
    files = [{"path": "src/a.py", "change_type": "added"}]
    capture = _capture(reported_changed_files=files)
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git",
        return_value=None,
    ):
        normalize(capture, proposal_id="p1", decision_id="d1",
                  workspace_path=Path("/nonexistent/workspace"))

    assert capture.changed_files_source == "event_stream"


def test_result_preserves_event_stream_provenance():
    files = [{"path": "src/a.py", "change_type": "added"}]
    capture = _capture(reported_changed_files=files)
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git",
        return_value=None,
    ):
        result = normalize(capture, proposal_id="p1", decision_id="d1",
                           workspace_path=Path("/nonexistent/workspace"))

    assert result.changed_files_source == "event_stream"
    assert result.changed_files_confidence == 0.5


def test_event_stream_change_type_preserved():
    files = [{"path": "src/b.py", "change_type": "deleted"}]
    capture = _capture(reported_changed_files=files)
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git",
        return_value=None,
    ):
        result = normalize(capture, proposal_id="p1", decision_id="d1",
                           workspace_path=Path("/nonexistent/workspace"))

    assert result.changed_files[0].change_type == "deleted"


# ---------------------------------------------------------------------------
# Changed-file evidence — unknown path
# ---------------------------------------------------------------------------


def test_changed_files_empty_when_unknown():
    capture = _capture()  # no reported_changed_files, no workspace
    result = normalize(capture, proposal_id="p1", decision_id="d1",
                       workspace_path=None)
    assert result.changed_files == []


def test_changed_files_source_unknown_when_no_workspace_no_events():
    capture = _capture()
    normalize(capture, proposal_id="p1", decision_id="d1", workspace_path=None)
    assert capture.changed_files_source == "unknown"


def test_empty_workspace_path_sentinel_skips_git():
    capture = _capture()
    result = normalize(capture, proposal_id="p1", decision_id="d1",
                       workspace_path=Path(""))
    assert result.changed_files == []
    assert capture.changed_files_source == "unknown"


# ---------------------------------------------------------------------------
# Git diff wins over event stream
# ---------------------------------------------------------------------------


def test_git_diff_takes_priority_over_event_stream():
    files = [{"path": "src/main.py", "change_type": "modified"}]
    capture = _capture(reported_changed_files=files)
    from operations_center.contracts.common import ChangedFileRef
    git_files = [ChangedFileRef(path="src/other.py", change_type="added")]
    with patch(
        "operations_center.backends.openclaw.normalize._discover_changed_files_via_git",
        return_value=git_files,
    ):
        result = normalize(capture, proposal_id="p1", decision_id="d1",
                           workspace_path=Path("/some/workspace"))

    assert result.changed_files[0].path == "src/other.py"
    assert capture.changed_files_source == "git_diff"


# ---------------------------------------------------------------------------
# Validation summary
# ---------------------------------------------------------------------------


def test_validation_skipped_by_default():
    result = normalize(_capture(), proposal_id="p1", decision_id="d1")
    assert result.validation.status == ValidationStatus.SKIPPED


def test_validation_passed():
    result = normalize(
        _capture(), proposal_id="p1", decision_id="d1",
        validation_ran=True, validation_passed=True,
    )
    assert result.validation.status == ValidationStatus.PASSED
    assert result.validation.commands_run == 1
    assert result.validation.commands_passed == 1


def test_validation_failed():
    result = normalize(
        _capture(), proposal_id="p1", decision_id="d1",
        validation_ran=True, validation_passed=False,
        validation_excerpt="ruff: 5 errors",
    )
    assert result.validation.status == ValidationStatus.FAILED
    assert result.validation.commands_failed == 1
    assert result.validation.failure_excerpt == "ruff: 5 errors"


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def test_artifacts_mapped_from_capture():
    arts = [
        OpenClawArtifactCapture(label="openclaw log", content="done", artifact_type="log_excerpt"),
    ]
    result = normalize(_capture(artifacts=arts), proposal_id="p1", decision_id="d1")
    assert len(result.artifacts) == 1
    assert result.artifacts[0].label == "openclaw log"


def test_unknown_artifact_type_defaults_to_log_excerpt():
    arts = [
        OpenClawArtifactCapture(label="raw", content="data", artifact_type="unknown_type"),
    ]
    result = normalize(_capture(artifacts=arts), proposal_id="p1", decision_id="d1")
    assert result.artifacts[0].artifact_type == ArtifactType.LOG_EXCERPT


def test_diff_artifact_type_preserved():
    arts = [
        OpenClawArtifactCapture(label="diff", content="--- a/main.py", artifact_type="diff"),
    ]
    result = normalize(_capture(artifacts=arts), proposal_id="p1", decision_id="d1")
    assert result.artifacts[0].artifact_type == ArtifactType.DIFF


# ---------------------------------------------------------------------------
# Events are NOT in ExecutionResult
# ---------------------------------------------------------------------------


def test_events_not_in_execution_result():
    events = [{"type": "tool_use", "name": "read_file"}, {"type": "message"}]
    capture = _capture(events=events)
    result = normalize(capture, proposal_id="p1", decision_id="d1")
    assert not hasattr(result, "events")
    assert capture.events == events  # still on capture for retention
