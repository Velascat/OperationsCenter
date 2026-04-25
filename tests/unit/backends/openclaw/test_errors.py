"""Tests for backends/openclaw/errors.py."""

from __future__ import annotations

import pytest

from operations_center.backends.openclaw.errors import build_failure_reason, categorize_failure
from operations_center.contracts.enums import FailureReasonCategory


# ---------------------------------------------------------------------------
# categorize_failure
# ---------------------------------------------------------------------------


def test_timeout_outcome_maps_to_timeout():
    cat = categorize_failure("timeout", "")
    assert cat == FailureReasonCategory.TIMEOUT


def test_timeout_signal_in_output():
    cat = categorize_failure("failure", "[timeout: process killed after 300s]")
    assert cat == FailureReasonCategory.TIMEOUT


def test_deadline_exceeded_signal():
    cat = categorize_failure("failure", "deadline exceeded: context limit reached")
    assert cat == FailureReasonCategory.TIMEOUT


def test_no_changes_signal():
    cat = categorize_failure("failure", "no changes detected in working tree")
    assert cat == FailureReasonCategory.NO_CHANGES


def test_nothing_to_commit_signal():
    cat = categorize_failure("failure", "nothing to commit, working tree clean")
    assert cat == FailureReasonCategory.NO_CHANGES


def test_conflict_signal():
    cat = categorize_failure("failure", "merge conflict in src/main.py")
    assert cat == FailureReasonCategory.CONFLICT


def test_auto_merge_failed_signal():
    cat = categorize_failure("failure", "auto-merge failed; fix conflicts and commit")
    assert cat == FailureReasonCategory.CONFLICT


def test_validation_failed_signal():
    cat = categorize_failure("failure", "validation failed: ruff check found 5 errors")
    assert cat == FailureReasonCategory.VALIDATION_FAILED


def test_test_failures_signal():
    cat = categorize_failure("failure", "tests failed: 3 test(s) failed")
    assert cat == FailureReasonCategory.VALIDATION_FAILED


def test_unknown_failure_maps_to_backend_error():
    cat = categorize_failure("failure", "something unexpected happened")
    assert cat == FailureReasonCategory.BACKEND_ERROR


def test_empty_output_maps_to_backend_error():
    cat = categorize_failure("failure", "")
    assert cat == FailureReasonCategory.BACKEND_ERROR


def test_timeout_outcome_wins_over_output_signals():
    cat = categorize_failure("timeout", "no changes detected, timeout")
    assert cat == FailureReasonCategory.TIMEOUT


def test_context_window_exceeded():
    cat = categorize_failure("failure", "context window exceeded: max tokens reached")
    assert cat == FailureReasonCategory.TIMEOUT


# ---------------------------------------------------------------------------
# build_failure_reason
# ---------------------------------------------------------------------------


def test_timeout_outcome_reason():
    reason = build_failure_reason("timeout", "", "")
    assert "timed out" in reason.lower() or "timeout" in reason.lower()


def test_partial_outcome_with_output():
    reason = build_failure_reason("partial", "step 3 failed", "")
    assert "partial" in reason.lower()
    assert "step 3 failed" in reason


def test_partial_outcome_without_output():
    reason = build_failure_reason("partial", "", "")
    assert "partial" in reason.lower()


def test_failure_with_error_text():
    reason = build_failure_reason("failure", "tool call failed", "")
    assert "tool call failed" in reason


def test_failure_with_output_text_fallback():
    reason = build_failure_reason("failure", "", "agent crashed")
    assert "agent crashed" in reason


def test_failure_no_output():
    reason = build_failure_reason("failure", "", "")
    assert "failure" in reason.lower() or "openclaw" in reason.lower()


def test_error_text_takes_precedence_over_output():
    reason = build_failure_reason("failure", "specific error", "general output")
    assert "specific error" in reason


def test_reason_is_truncated_at_300_chars():
    long_text = "x" * 400
    reason = build_failure_reason("failure", long_text, "")
    assert len(reason) < 500
