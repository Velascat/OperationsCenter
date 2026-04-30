# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for backends/archon/errors.py."""

from __future__ import annotations


from operations_center.backends.archon.errors import build_failure_reason, categorize_failure
from operations_center.contracts.enums import FailureReasonCategory


# ---------------------------------------------------------------------------
# categorize_failure — outcome-based
# ---------------------------------------------------------------------------


def test_timeout_outcome_gives_timeout_category():
    cat = categorize_failure("timeout", "")
    assert cat == FailureReasonCategory.TIMEOUT


def test_failure_outcome_with_timeout_signal_gives_timeout():
    cat = categorize_failure("failure", "[timeout: process killed after 300s]")
    assert cat == FailureReasonCategory.TIMEOUT


def test_deadline_exceeded_signal():
    cat = categorize_failure("failure", "deadline exceeded")
    assert cat == FailureReasonCategory.TIMEOUT


def test_no_changes_signal():
    cat = categorize_failure("failure", "no changes detected")
    assert cat == FailureReasonCategory.NO_CHANGES


def test_nothing_to_commit_signal():
    cat = categorize_failure("failure", "nothing to commit, working tree clean")
    assert cat == FailureReasonCategory.NO_CHANGES


def test_conflict_signal():
    cat = categorize_failure("failure", "merge conflict in src/api.py")
    assert cat == FailureReasonCategory.CONFLICT


def test_validation_failed_signal():
    cat = categorize_failure("failure", "validation failed: ruff found 3 errors")
    assert cat == FailureReasonCategory.VALIDATION_FAILED


def test_checks_failed_signal():
    cat = categorize_failure("failure", "checks failed")
    assert cat == FailureReasonCategory.VALIDATION_FAILED


def test_unknown_failure_gives_backend_error():
    cat = categorize_failure("failure", "something went wrong")
    assert cat == FailureReasonCategory.BACKEND_ERROR


def test_partial_outcome_gives_backend_error():
    cat = categorize_failure("partial", "partial completion")
    assert cat == FailureReasonCategory.BACKEND_ERROR


def test_empty_output_gives_backend_error():
    cat = categorize_failure("failure", "")
    assert cat == FailureReasonCategory.BACKEND_ERROR


# ---------------------------------------------------------------------------
# build_failure_reason
# ---------------------------------------------------------------------------


def test_timeout_reason():
    reason = build_failure_reason("timeout", "", "")
    assert "timed out" in reason.lower()


def test_partial_reason():
    reason = build_failure_reason("partial", "", "")
    assert "partial" in reason.lower()


def test_partial_with_error_text():
    reason = build_failure_reason("partial", "step 3 failed", "")
    assert "step 3 failed" in reason


def test_failure_with_error_text():
    reason = build_failure_reason("failure", "workflow step aborted", "")
    assert "workflow step aborted" in reason


def test_failure_falls_back_to_output_text():
    reason = build_failure_reason("failure", "", "archon: exit code 1")
    assert "archon: exit code 1" in reason


def test_empty_reason_has_default():
    reason = build_failure_reason("failure", "", "")
    assert reason  # non-empty


def test_reason_truncated_at_300():
    long_error = "e" * 1000
    reason = build_failure_reason("failure", long_error, "")
    assert len(reason) < 400


def test_reason_prefers_error_over_output():
    reason = build_failure_reason("failure", "error detail", "output detail")
    assert "error detail" in reason
    assert "output detail" not in reason
