# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for kodo error categorization."""

from __future__ import annotations

from operations_center.backends.kodo.errors import build_failure_reason, categorize_failure
from operations_center.contracts.enums import FailureReasonCategory


class TestCategorizeFailure:
    def test_timeout_signal_detected(self):
        cat = categorize_failure(-1, "[timeout: process group killed after 300s]")
        assert cat == FailureReasonCategory.TIMEOUT

    def test_quota_429_signal(self):
        cat = categorize_failure(1, "Error: 429 Too Many Requests")
        assert cat == FailureReasonCategory.BACKEND_ERROR

    def test_no_changes_signal(self):
        cat = categorize_failure(0, "nothing to commit, working tree clean")
        assert cat == FailureReasonCategory.NO_CHANGES

    def test_conflict_signal(self):
        cat = categorize_failure(1, "Auto-merge failed; fix conflicts and commit")
        assert cat == FailureReasonCategory.CONFLICT

    def test_generic_nonzero_exit(self):
        cat = categorize_failure(1, "some unknown error")
        assert cat == FailureReasonCategory.BACKEND_ERROR

    def test_exit_zero_flagged_as_failure_returns_unknown(self):
        cat = categorize_failure(0, "some unusual exit 0 failure")
        assert cat == FailureReasonCategory.UNKNOWN

    def test_usage_limit_is_backend_error(self):
        cat = categorize_failure(1, "You've hit your limit for this billing period")
        assert cat == FailureReasonCategory.BACKEND_ERROR


class TestBuildFailureReason:
    def test_includes_exit_code(self):
        reason = build_failure_reason(1, "something went wrong", "")
        assert "1" in reason

    def test_prefers_stderr_over_stdout(self):
        reason = build_failure_reason(1, "error message", "stdout output")
        assert "error message" in reason

    def test_falls_back_to_stdout(self):
        reason = build_failure_reason(1, "", "stdout content")
        assert "stdout content" in reason

    def test_no_output_returns_generic(self):
        reason = build_failure_reason(2, "", "")
        assert "2" in reason

    def test_truncates_long_output(self):
        reason = build_failure_reason(1, "x" * 500, "")
        assert len(reason) < 600
