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
# KNOWN: files enumerated
# ---------------------------------------------------------------------------


def test_non_empty_changed_files_status_is_known():
    result = make_result(changed_files=[make_changed_file("src/main.py")])
    ev = normalize_changed_files(result)
    assert ev.status == ChangedFilesStatus.KNOWN


def test_known_preserves_files():
    files = [make_changed_file("src/a.py"), make_changed_file("src/b.py")]
    result = make_result(changed_files=files)
    ev = normalize_changed_files(result)
    assert len(ev.files) == 2
    paths = {f.path for f in ev.files}
    assert paths == {"src/a.py", "src/b.py"}


def test_known_source_is_backend_manifest():
    result = make_result(changed_files=[make_changed_file()])
    ev = normalize_changed_files(result)
    assert ev.source == "backend_manifest"


def test_known_confidence_is_1():
    result = make_result(changed_files=[make_changed_file()])
    ev = normalize_changed_files(result)
    assert ev.confidence == 1.0


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


# ---------------------------------------------------------------------------
# Model is frozen
# ---------------------------------------------------------------------------


def test_changed_files_evidence_is_frozen():
    result = make_result(changed_files=[make_changed_file()])
    ev = normalize_changed_files(result)
    with pytest.raises(Exception):
        ev.status = ChangedFilesStatus.UNKNOWN  # type: ignore[misc]
