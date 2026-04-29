"""Tests for observability/trace.py — RunReportBuilder and ExecutionTrace."""

from __future__ import annotations

import pytest

from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    ValidationStatus,
)
from operations_center.observability.recorder import ExecutionRecorder
from operations_center.observability.trace import ExecutionTrace, RunReportBuilder



@pytest.fixture
def recorder() -> ExecutionRecorder:
    return ExecutionRecorder()


@pytest.fixture
def builder() -> RunReportBuilder:
    return RunReportBuilder()


def _trace(result, recorder, builder, **kw):
    record = recorder.record(result, **kw)
    return builder.build_report(record)


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_build_report_returns_execution_trace(recorder, builder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    trace = builder.build_report(record)
    assert isinstance(trace, ExecutionTrace)


def test_trace_id_is_nonempty(recorder, builder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    trace = builder.build_report(record)
    assert trace.trace_id


def test_record_id_links_to_record(recorder, builder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    trace = builder.build_report(record)
    assert trace.record_id == record.record_id


def test_status_matches_result(recorder, builder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    trace = builder.build_report(record)
    assert trace.status == ExecutionStatus.SUCCEEDED


def test_generated_at_is_set(recorder, builder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    trace = builder.build_report(record)
    assert trace.generated_at is not None


# ---------------------------------------------------------------------------
# Headline
# ---------------------------------------------------------------------------


def test_headline_contains_status(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder)
    assert "SUCCEEDED" in trace.headline


def test_headline_contains_backend(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder, backend="kodo")
    assert "kodo" in trace.headline


def test_headline_contains_lane(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder, lane="claude_cli")
    assert "claude_cli" in trace.headline


def test_headline_contains_run_prefix(recorder, builder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    trace = builder.build_report(record)
    assert record.run_id[:8] in trace.headline


def test_headline_unknown_when_no_backend(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder)
    assert "unknown" in trace.headline


def test_headline_for_failure(recorder, builder, failed_result_with_logs):
    trace = _trace(failed_result_with_logs, recorder, builder)
    assert "FAILED" in trace.headline


def test_headline_for_timeout(recorder, builder, timeout_result):
    trace = _trace(timeout_result, recorder, builder)
    assert "TIMED_OUT" in trace.headline


# ---------------------------------------------------------------------------
# Key artifacts
# ---------------------------------------------------------------------------


def test_key_artifacts_are_primary_only(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder)
    types = {a.artifact_type for a in trace.key_artifacts}
    assert ArtifactType.DIFF in types
    assert ArtifactType.VALIDATION_REPORT in types
    assert ArtifactType.LOG_EXCERPT not in types


def test_key_artifacts_empty_when_no_primary(recorder, builder, sparse_result):
    trace = _trace(sparse_result, recorder, builder)
    assert trace.key_artifacts == []


# ---------------------------------------------------------------------------
# Changed files summary
# ---------------------------------------------------------------------------


def test_changed_files_summary_known(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder)
    assert "2 files" in trace.changed_files_summary
    assert "git_diff" in trace.changed_files_summary


def test_changed_files_summary_none(recorder, builder, no_changes_result):
    trace = _trace(no_changes_result, recorder, builder)
    assert "no files changed" in trace.changed_files_summary


def test_changed_files_summary_not_applicable(recorder, builder, policy_blocked_result):
    trace = _trace(policy_blocked_result, recorder, builder)
    assert "not applicable" in trace.changed_files_summary


def test_changed_files_summary_unknown(recorder, builder, sparse_result):
    trace = _trace(sparse_result, recorder, builder)
    assert "unknown" in trace.changed_files_summary


# ---------------------------------------------------------------------------
# Validation summary in trace
# ---------------------------------------------------------------------------


def test_validation_summary_passed(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder)
    assert trace.validation_summary.status == ValidationStatus.PASSED


def test_validation_summary_skipped(recorder, builder, failed_result_with_logs):
    trace = _trace(failed_result_with_logs, recorder, builder)
    assert trace.validation_summary.status == ValidationStatus.SKIPPED


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_warning_for_unknown_changed_files(recorder, builder, sparse_result):
    trace = _trace(sparse_result, recorder, builder)
    assert any("changed-file manifest" in w for w in trace.warnings)


def test_warning_for_skipped_validation(recorder, builder, failed_result_with_logs):
    trace = _trace(failed_result_with_logs, recorder, builder)
    assert any("validation was skipped" in w for w in trace.warnings)


def test_warning_for_no_primary_artifacts(recorder, builder, sparse_result):
    trace = _trace(sparse_result, recorder, builder)
    assert any("no primary artifacts" in w for w in trace.warnings)


def test_warning_for_no_changes(recorder, builder, no_changes_result):
    trace = _trace(no_changes_result, recorder, builder)
    assert any("no file changes" in w for w in trace.warnings)


def test_no_spurious_warnings_on_rich_successful_run(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder)
    # should not have changed-file or primary-artifact warnings
    assert not any("changed-file manifest" in w for w in trace.warnings)
    assert not any("no primary artifacts" in w for w in trace.warnings)


def test_no_changed_file_warning_for_not_applicable(recorder, builder, policy_blocked_result):
    trace = _trace(policy_blocked_result, recorder, builder)
    assert not any("changed-file manifest" in w for w in trace.warnings)


# ---------------------------------------------------------------------------
# Backend detail refs passed through
# ---------------------------------------------------------------------------


def test_backend_detail_refs_in_trace(recorder, builder, successful_rich_result):
    from operations_center.observability.models import BackendDetailRef
    ref = BackendDetailRef(detail_type="stderr_log", path="/tmp/stderr.txt")
    record = recorder.record(successful_rich_result, raw_detail_refs=[ref])
    trace = builder.build_report(record)
    assert len(trace.backend_detail_refs) == 1
    assert trace.backend_detail_refs[0].detail_type == "stderr_log"


# ---------------------------------------------------------------------------
# Frozen
# ---------------------------------------------------------------------------


def test_execution_trace_is_frozen(recorder, builder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    trace = builder.build_report(record)
    with pytest.raises(Exception):
        trace.headline = "nope"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Summary content
# ---------------------------------------------------------------------------


def test_summary_mentions_run_id(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder)
    assert successful_rich_result.run_id[:8] in trace.summary


def test_summary_mentions_failure_reason(recorder, builder, failed_result_with_logs):
    trace = _trace(failed_result_with_logs, recorder, builder)
    assert "failed" in trace.summary.lower()


def test_summary_mentions_changed_files_count(recorder, builder, successful_rich_result):
    trace = _trace(successful_rich_result, recorder, builder)
    assert "2 files" in trace.summary
