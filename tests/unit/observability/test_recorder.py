# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for observability/recorder.py — ExecutionRecorder."""

from __future__ import annotations

import pytest

from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.observability.changed_files import ChangedFilesStatus
from operations_center.observability.models import BackendDetailRef, ExecutionRecord
from operations_center.observability.recorder import ExecutionRecorder

from .conftest import (
    make_changed_file,
    make_result,
)


@pytest.fixture
def recorder() -> ExecutionRecorder:
    return ExecutionRecorder()


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_record_returns_execution_record(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert isinstance(record, ExecutionRecord)


def test_record_id_is_nonempty(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.record_id


def test_record_id_is_unique(recorder, successful_rich_result):
    r1 = recorder.record(successful_rich_result)
    r2 = recorder.record(successful_rich_result)
    assert r1.record_id != r2.record_id


def test_record_preserves_result(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.result is successful_rich_result


def test_run_id_matches_result(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.run_id == successful_rich_result.run_id


def test_proposal_id_matches_result(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.proposal_id == successful_rich_result.proposal_id


def test_decision_id_matches_result(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.decision_id == successful_rich_result.decision_id


def test_recorded_at_is_set(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.recorded_at is not None


# ---------------------------------------------------------------------------
# Backend / lane
# ---------------------------------------------------------------------------


def test_backend_propagated(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result, backend="kodo")
    assert record.backend == "kodo"


def test_lane_propagated(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result, lane="claude_cli")
    assert record.lane == "claude_cli"


def test_backend_none_when_not_provided(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.backend is None


# ---------------------------------------------------------------------------
# Artifact index
# ---------------------------------------------------------------------------


def test_artifact_index_present(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.artifact_index is not None


def test_primary_artifacts_classified(recorder, successful_rich_result):
    # successful_rich_result has DIFF and VALIDATION_REPORT (primary) + LOG_EXCERPT (supplemental)
    record = recorder.record(successful_rich_result)
    types = {a.artifact_type for a in record.artifact_index.primary_artifacts}
    assert ArtifactType.DIFF in types
    assert ArtifactType.VALIDATION_REPORT in types


def test_supplemental_artifacts_classified(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    types = {a.artifact_type for a in record.artifact_index.supplemental_artifacts}
    assert ArtifactType.LOG_EXCERPT in types


# ---------------------------------------------------------------------------
# Changed files evidence
# ---------------------------------------------------------------------------


def test_changed_files_evidence_present(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.changed_files_evidence is not None


def test_known_changed_files_evidence(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.changed_files_evidence.status == ChangedFilesStatus.KNOWN
    assert len(record.changed_files_evidence.files) == 2


def test_inferred_changed_files_evidence(recorder):
    result = make_result(
        changed_files=[make_changed_file("src/inferred.py")],
        changed_files_source="event_stream",
        changed_files_confidence=0.5,
    )
    record = recorder.record(result)
    assert record.changed_files_evidence.status == ChangedFilesStatus.INFERRED
    assert record.changed_files_evidence.source == "event_stream"


def test_policy_blocked_gives_not_applicable(recorder, policy_blocked_result):
    record = recorder.record(policy_blocked_result)
    assert record.changed_files_evidence.status == ChangedFilesStatus.NOT_APPLICABLE


def test_no_changes_category_gives_none(recorder, no_changes_result):
    record = recorder.record(no_changes_result)
    assert record.changed_files_evidence.status == ChangedFilesStatus.NONE


def test_sparse_result_changed_files_unknown(recorder, sparse_result):
    record = recorder.record(sparse_result)
    assert record.changed_files_evidence.status == ChangedFilesStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Validation evidence
# ---------------------------------------------------------------------------


def test_validation_evidence_present(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.validation_evidence is not None


def test_validation_passed_evidence(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.validation_evidence.status == ValidationStatus.PASSED
    assert record.validation_evidence.checks_run == 2
    assert record.validation_evidence.checks_passed == 2


def test_validation_skipped_on_failed_result(recorder, failed_result_with_logs):
    record = recorder.record(failed_result_with_logs)
    assert record.validation_evidence.status == ValidationStatus.SKIPPED


# ---------------------------------------------------------------------------
# Backend detail refs
# ---------------------------------------------------------------------------


def test_backend_detail_refs_default_empty(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.backend_detail_refs == []


def test_backend_detail_refs_propagated(recorder, successful_rich_result):
    ref = BackendDetailRef(detail_type="stderr_log", path="/tmp/run/stderr.txt")
    record = recorder.record(successful_rich_result, raw_detail_refs=[ref])
    assert len(record.backend_detail_refs) == 1
    assert record.backend_detail_refs[0].detail_type == "stderr_log"


def test_multiple_backend_detail_refs(recorder, successful_rich_result):
    refs = [
        BackendDetailRef(detail_type="stderr_log"),
        BackendDetailRef(detail_type="jsonl_stream"),
    ]
    record = recorder.record(successful_rich_result, raw_detail_refs=refs)
    assert len(record.backend_detail_refs) == 2


# ---------------------------------------------------------------------------
# Notes and metadata
# ---------------------------------------------------------------------------


def test_notes_propagated(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result, notes="nightly lint cycle")
    assert record.notes == "nightly lint cycle"


def test_metadata_propagated(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result, metadata={"trigger": "cron"})
    assert record.metadata["trigger"] == "cron"


def test_notes_default_empty(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.notes == ""


def test_metadata_default_empty(recorder, successful_rich_result):
    record = recorder.record(successful_rich_result)
    assert record.metadata == {}


# ---------------------------------------------------------------------------
# Sparse / minimal results still produce valid records
# ---------------------------------------------------------------------------


def test_sparse_result_produces_valid_record(recorder, sparse_result):
    record = recorder.record(sparse_result)
    assert isinstance(record, ExecutionRecord)
    assert record.artifact_index is not None
    assert record.changed_files_evidence is not None
    assert record.validation_evidence is not None


def test_timeout_result_produces_valid_record(recorder):
    result = make_result(
        status=ExecutionStatus.TIMED_OUT,
        success=False,
        failure_category=FailureReasonCategory.TIMEOUT,
        artifacts=[],
    )
    record = recorder.record(result)
    assert isinstance(record, ExecutionRecord)
