"""Tests for openclaw_shell/status.py — pure status derivation functions."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from control_plane.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from control_plane.observability.recorder import ExecutionRecorder
from control_plane.observability.trace import RunReportBuilder
from control_plane.openclaw_shell.models import ShellInspectionResult, ShellStatusSummary
from control_plane.openclaw_shell.status import (
    inspection_from_record,
    status_from_record,
    status_from_result_only,
)

from ..observability.conftest import (
    make_artifact,
    make_changed_file,
    make_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_recorder = ExecutionRecorder()
_builder = RunReportBuilder()


def _make_record_and_trace(result, backend="kodo", lane="claude_cli"):
    record = _recorder.record(result, backend=backend, lane=lane)
    trace = _builder.build_report(record)
    return record, trace


def _success_result(**kw):
    defaults = dict(
        run_id="run-s01",
        proposal_id="prop-s01",
        decision_id="dec-s01",
        status=ExecutionStatus.SUCCESS,
        success=True,
        changed_files=[make_changed_file("src/main.py")],
        validation_status=ValidationStatus.PASSED,
        validation_commands_run=1,
        validation_commands_passed=1,
    )
    defaults.update(kw)
    return make_result(**defaults)


def _failed_result(**kw):
    return make_result(
        run_id="run-f01",
        proposal_id="prop-f01",
        decision_id="dec-f01",
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason="kodo exited 1: tool call failed",
        **kw,
    )


# ---------------------------------------------------------------------------
# status_from_record — return type and identifiers
# ---------------------------------------------------------------------------


def test_status_from_record_returns_summary():
    record, trace = _make_record_and_trace(_success_result())
    result = status_from_record(record, trace)
    assert isinstance(result, ShellStatusSummary)


def test_status_from_record_identifiers():
    record, trace = _make_record_and_trace(_success_result())
    summary = status_from_record(record, trace)
    assert summary.run_id == "run-s01"
    assert summary.proposal_id == "prop-s01"
    assert summary.decision_id == "dec-s01"


def test_status_from_record_success_fields():
    record, trace = _make_record_and_trace(_success_result())
    summary = status_from_record(record, trace)
    assert summary.success is True
    assert summary.status == ExecutionStatus.SUCCESS.value


def test_status_from_record_failure_fields():
    record, trace = _make_record_and_trace(_failed_result())
    summary = status_from_record(record, trace)
    assert summary.success is False
    assert summary.status == ExecutionStatus.FAILED.value


def test_status_from_record_headline_from_trace():
    record, trace = _make_record_and_trace(_success_result())
    summary = status_from_record(record, trace)
    assert summary.headline == trace.headline
    assert len(summary.headline) > 0


def test_status_from_record_summary_from_trace():
    record, trace = _make_record_and_trace(_success_result())
    summary = status_from_record(record, trace)
    assert summary.summary == trace.summary


def test_status_from_record_lane_backend():
    record, trace = _make_record_and_trace(_success_result(), backend="kodo", lane="claude_cli")
    summary = status_from_record(record, trace)
    assert summary.selected_lane == "claude_cli"
    assert summary.selected_backend == "kodo"


def test_status_from_record_recorded_at_set():
    record, trace = _make_record_and_trace(_success_result())
    summary = status_from_record(record, trace)
    assert summary.recorded_at is not None
    assert isinstance(summary.recorded_at, datetime)


def test_status_from_record_artifact_count():
    result = _success_result(artifacts=[
        make_artifact(ArtifactType.DIFF, "diff", "content"),
        make_artifact(ArtifactType.LOG_EXCERPT, "log", "log"),
    ])
    record, trace = _make_record_and_trace(result)
    summary = status_from_record(record, trace)
    assert summary.artifact_count == 2


def test_status_from_record_validation_status_passed():
    record, trace = _make_record_and_trace(_success_result())
    summary = status_from_record(record, trace)
    assert summary.validation_status == ValidationStatus.PASSED.value


def test_status_from_record_changed_files_status_known():
    result = _success_result(changed_files=[make_changed_file("src/main.py")])
    record, trace = _make_record_and_trace(result)
    summary = status_from_record(record, trace)
    assert summary.changed_files_status != "unknown"


def test_status_from_record_is_frozen():
    record, trace = _make_record_and_trace(_success_result())
    summary = status_from_record(record, trace)
    with pytest.raises(Exception):
        summary.status = "hacked"


# ---------------------------------------------------------------------------
# status_from_result_only — lightweight path
# ---------------------------------------------------------------------------


def test_status_from_result_only_returns_summary():
    result = _success_result()
    summary = status_from_result_only(result, lane="claude_cli", backend="kodo")
    assert isinstance(summary, ShellStatusSummary)


def test_status_from_result_only_identifiers():
    result = _success_result()
    summary = status_from_result_only(result)
    assert summary.run_id == "run-s01"
    assert summary.proposal_id == "prop-s01"
    assert summary.decision_id == "dec-s01"


def test_status_from_result_only_success():
    result = _success_result()
    summary = status_from_result_only(result)
    assert summary.success is True
    assert summary.status == ExecutionStatus.SUCCESS.value


def test_status_from_result_only_failure():
    result = _failed_result()
    summary = status_from_result_only(result)
    assert summary.success is False
    assert summary.status == ExecutionStatus.FAILED.value


def test_status_from_result_only_headline_contains_status():
    result = _success_result()
    summary = status_from_result_only(result, lane="claude_cli", backend="kodo")
    assert "SUCCESS" in summary.headline.upper()


def test_status_from_result_only_headline_contains_run_id():
    result = _success_result()
    summary = status_from_result_only(result, lane="claude_cli", backend="kodo")
    assert "run-s01"[:8] in summary.headline


def test_status_from_result_only_no_lane_backend():
    result = _success_result()
    summary = status_from_result_only(result)
    assert summary.selected_lane is None
    assert summary.selected_backend is None


def test_status_from_result_only_with_lane_backend():
    result = _success_result()
    summary = status_from_result_only(result, lane="aider_local", backend="kodo")
    assert summary.selected_lane == "aider_local"
    assert summary.selected_backend == "kodo"


def test_status_from_result_only_recorded_at_none():
    result = _success_result()
    summary = status_from_result_only(result)
    assert summary.recorded_at is None


def test_status_from_result_only_changed_files_known():
    result = _success_result(changed_files=[make_changed_file("src/a.py")])
    summary = status_from_result_only(result)
    assert summary.changed_files_status == "known"


def test_status_from_result_only_changed_files_unknown():
    result = _failed_result()
    summary = status_from_result_only(result)
    assert summary.changed_files_status == "unknown"


def test_status_from_result_only_artifact_count():
    result = _success_result(artifacts=[
        make_artifact(ArtifactType.DIFF, "diff", "c"),
        make_artifact(ArtifactType.LOG_EXCERPT, "log", "c"),
        make_artifact(ArtifactType.VALIDATION_REPORT, "report", "c"),
    ])
    summary = status_from_result_only(result)
    assert summary.artifact_count == 3


def test_status_from_result_only_failure_reason_in_summary():
    result = _failed_result()
    summary = status_from_result_only(result)
    assert "kodo exited 1" in summary.summary


def test_status_from_result_only_is_frozen():
    result = _success_result()
    summary = status_from_result_only(result)
    with pytest.raises(Exception):
        summary.success = False


# ---------------------------------------------------------------------------
# inspection_from_record
# ---------------------------------------------------------------------------


def test_inspection_from_record_returns_type():
    record, trace = _make_record_and_trace(_success_result())
    result = inspection_from_record(record, trace)
    assert isinstance(result, ShellInspectionResult)


def test_inspection_from_record_identifiers():
    record, trace = _make_record_and_trace(_success_result())
    r = inspection_from_record(record, trace)
    assert r.run_id == "run-s01"
    assert r.proposal_id == "prop-s01"
    assert r.decision_id == "dec-s01"


def test_inspection_from_record_status():
    record, trace = _make_record_and_trace(_success_result())
    r = inspection_from_record(record, trace)
    assert r.status == ExecutionStatus.SUCCESS.value


def test_inspection_from_record_headline_and_summary():
    record, trace = _make_record_and_trace(_success_result())
    r = inspection_from_record(record, trace)
    assert r.headline == trace.headline
    assert r.summary == trace.summary


def test_inspection_from_record_warnings_list():
    record, trace = _make_record_and_trace(_failed_result())
    r = inspection_from_record(record, trace)
    assert isinstance(r.warnings, list)


def test_inspection_from_record_trace_id_set():
    record, trace = _make_record_and_trace(_success_result())
    r = inspection_from_record(record, trace)
    assert r.trace_id == trace.trace_id


def test_inspection_from_record_record_id_set():
    record, trace = _make_record_and_trace(_success_result())
    r = inspection_from_record(record, trace)
    assert r.record_id == record.record_id


def test_inspection_from_record_lane_backend():
    record, trace = _make_record_and_trace(_success_result(), backend="kodo", lane="claude_cli")
    r = inspection_from_record(record, trace)
    assert r.selected_lane == "claude_cli"
    assert r.selected_backend == "kodo"


def test_inspection_from_record_artifact_count():
    result = _success_result(artifacts=[
        make_artifact(ArtifactType.DIFF, "diff", "c"),
        make_artifact(ArtifactType.VALIDATION_REPORT, "report", "c"),
    ])
    record, trace = _make_record_and_trace(result)
    r = inspection_from_record(record, trace)
    assert r.artifact_count == 2


def test_inspection_from_record_primary_artifact_count():
    result = _success_result(artifacts=[
        make_artifact(ArtifactType.DIFF, "diff", "c"),
        make_artifact(ArtifactType.VALIDATION_REPORT, "report", "c"),
        make_artifact(ArtifactType.LOG_EXCERPT, "log", "c"),
    ])
    record, trace = _make_record_and_trace(result)
    r = inspection_from_record(record, trace)
    assert r.primary_artifact_count >= 0
    assert r.primary_artifact_count <= r.artifact_count


def test_inspection_from_record_recorded_at():
    record, trace = _make_record_and_trace(_success_result())
    r = inspection_from_record(record, trace)
    assert r.recorded_at is not None


def test_inspection_from_record_is_frozen():
    record, trace = _make_record_and_trace(_success_result())
    r = inspection_from_record(record, trace)
    with pytest.raises(Exception):
        r.status = "hacked"
