"""Tests for observability/changed_files.py — normalize_changed_files."""

from __future__ import annotations

import pytest

from control_plane.contracts.enums import ExecutionStatus, FailureReasonCategory
from control_plane.observability.changed_files import (
    ChangedFilesStatus,
    normalize_changed_files,
)

from .conftest import make_changed_file, make_result


# ---------------------------------------------------------------------------
# KNOWN: files with authoritative provenance
# ---------------------------------------------------------------------------


def test_non_empty_changed_files_status_is_known():
    result = make_result(
        changed_files=[make_changed_file("src/main.py")],
        changed_files_source="git_diff",
        changed_files_confidence=1.0,
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.KNOWN


def test_known_preserves_files():
    files = [make_changed_file("src/a.py"), make_changed_file("src/b.py")]
    result = make_result(changed_files=files, changed_files_source="backend_manifest")
    ev = normalize_changed_files(result)
    assert len(ev.files) == 2
    paths = {f.path for f in ev.files}
    assert paths == {"src/a.py", "src/b.py"}


def test_known_source_is_explicit_provenance():
    result = make_result(changed_files=[make_changed_file()], changed_files_source="backend_manifest")
    ev = normalize_changed_files(result)
    assert ev.source == "backend_manifest"


def test_known_confidence_is_1():
    result = make_result(
        changed_files=[make_changed_file()],
        changed_files_source="git_diff",
        changed_files_confidence=1.0,
    )
    ev = normalize_changed_files(result)
    assert ev.confidence == 1.0


def test_non_empty_changed_files_without_provenance_is_not_upgraded_to_known():
    result = make_result(changed_files=[make_changed_file()], changed_files_source=None)
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.UNKNOWN
    assert ev.confidence == 0.0


# ---------------------------------------------------------------------------
# INFERRED: non-authoritative evidence
# ---------------------------------------------------------------------------


def test_event_stream_changed_files_are_inferred():
    result = make_result(
        changed_files=[make_changed_file("src/inferred.py")],
        changed_files_source="event_stream",
        changed_files_confidence=0.5,
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.INFERRED
    assert ev.source == "event_stream"
    assert ev.confidence == 0.5


# ---------------------------------------------------------------------------
# NONE: confirmed no changes
# ---------------------------------------------------------------------------


def test_no_changes_category_gives_none_status():
    result = make_result(
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.NO_CHANGES,
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.NONE


def test_no_changes_files_list_is_empty():
    result = make_result(failure_category=FailureReasonCategory.NO_CHANGES)
    ev = normalize_changed_files(result)
    assert ev.files == []


def test_no_changes_source_is_confirmed_empty():
    result = make_result(failure_category=FailureReasonCategory.NO_CHANGES)
    ev = normalize_changed_files(result)
    assert ev.source == "backend_confirmed_empty"


# ---------------------------------------------------------------------------
# NOT_APPLICABLE: policy blocked
# ---------------------------------------------------------------------------


def test_policy_blocked_status_is_not_applicable():
    result = make_result(
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.POLICY_BLOCKED,
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.NOT_APPLICABLE


def test_policy_blocked_files_list_is_empty():
    result = make_result(failure_category=FailureReasonCategory.POLICY_BLOCKED)
    ev = normalize_changed_files(result)
    assert ev.files == []


def test_policy_blocked_source_is_policy_blocked():
    result = make_result(failure_category=FailureReasonCategory.POLICY_BLOCKED)
    ev = normalize_changed_files(result)
    assert ev.source == "policy_blocked"


def test_unsupported_request_status_is_not_applicable():
    result = make_result(
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.UNSUPPORTED_REQUEST,
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.NOT_APPLICABLE


def test_unsupported_request_source_is_adapter_unsupported():
    result = make_result(failure_category=FailureReasonCategory.UNSUPPORTED_REQUEST)
    ev = normalize_changed_files(result)
    assert ev.source == "adapter_unsupported"


def test_unsupported_request_notes_do_not_blame_policy():
    result = make_result(failure_category=FailureReasonCategory.UNSUPPORTED_REQUEST)
    ev = normalize_changed_files(result)
    assert ev.notes is not None
    assert "policy" not in ev.notes.lower()


# ---------------------------------------------------------------------------
# UNKNOWN: backend did not report
# ---------------------------------------------------------------------------


def test_empty_files_and_no_special_category_is_unknown():
    result = make_result(
        status=ExecutionStatus.SUCCESS,
        success=True,
        changed_files=[],
        failure_category=None,
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.UNKNOWN


def test_failed_with_backend_error_and_no_files_is_unknown():
    result = make_result(
        status=ExecutionStatus.FAILED,
        success=False,
        changed_files=[],
        failure_category=FailureReasonCategory.BACKEND_ERROR,
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.UNKNOWN


def test_unknown_confidence_is_0():
    result = make_result(changed_files=[], failure_category=None)
    ev = normalize_changed_files(result)
    assert ev.confidence == 0.0


def test_unknown_source_is_none():
    result = make_result(changed_files=[], failure_category=None)
    ev = normalize_changed_files(result)
    assert ev.source == "none"


def test_authoritative_empty_git_diff_is_none_not_unknown():
    result = make_result(
        changed_files=[],
        changed_files_source="git_diff",
        changed_files_confidence=1.0,
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.NONE
    assert ev.source == "git_diff"


# ---------------------------------------------------------------------------
# Priority: policy_blocked takes precedence over changed_files
# ---------------------------------------------------------------------------


def test_policy_blocked_takes_precedence_over_changed_files():
    """Even if changed_files were somehow populated, policy_blocked wins."""
    result = make_result(
        failure_category=FailureReasonCategory.POLICY_BLOCKED,
        changed_files=[make_changed_file()],
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.NOT_APPLICABLE


def test_unsupported_request_takes_precedence_over_changed_files():
    result = make_result(
        failure_category=FailureReasonCategory.UNSUPPORTED_REQUEST,
        changed_files=[make_changed_file()],
    )
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.NOT_APPLICABLE
    assert ev.source == "adapter_unsupported"


# ---------------------------------------------------------------------------
# Model is frozen
# ---------------------------------------------------------------------------


def test_changed_files_evidence_is_frozen():
    result = make_result(changed_files=[make_changed_file()])
    ev = normalize_changed_files(result)
    with pytest.raises(Exception):
        ev.status = ChangedFilesStatus.UNKNOWN  # type: ignore[misc]
