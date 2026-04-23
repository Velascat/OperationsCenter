"""Tests for canonical enum types."""

from __future__ import annotations

import json

import pytest

from control_plane.contracts.enums import (
    ArtifactType,
    BackendName,
    ExecutionMode,
    ExecutionStatus,
    FailureReasonCategory,
    LaneName,
    Priority,
    RiskLevel,
    TaskType,
    ValidationStatus,
)


class TestTaskType:
    def test_all_members_are_strings(self):
        for member in TaskType:
            assert isinstance(member.value, str)

    def test_round_trip_from_string(self):
        assert TaskType("lint_fix") is TaskType.LINT_FIX
        assert TaskType("bug_fix") is TaskType.BUG_FIX
        assert TaskType("unknown") is TaskType.UNKNOWN

    def test_json_serialises_as_string(self):
        data = json.dumps({"task_type": TaskType.DOCUMENTATION})
        assert '"documentation"' in data


class TestLaneName:
    def test_all_three_lanes_present(self):
        names = {m.value for m in LaneName}
        assert names == {"claude_cli", "codex_cli", "aider_local"}

    def test_round_trip(self):
        assert LaneName("aider_local") is LaneName.AIDER_LOCAL


class TestBackendName:
    def test_round_trip(self):
        assert BackendName("kodo") is BackendName.KODO
        assert BackendName("direct_local") is BackendName.DIRECT_LOCAL
        assert BackendName("archon_then_kodo") is BackendName.ARCHON_THEN_KODO


class TestExecutionStatus:
    def test_terminal_states_present(self):
        terminals = {"success", "failed", "skipped", "timeout", "cancelled"}
        assert terminals <= {m.value for m in ExecutionStatus}

    def test_in_progress_states_present(self):
        assert ExecutionStatus.PENDING.value == "pending"
        assert ExecutionStatus.RUNNING.value == "running"


class TestValidationStatus:
    def test_all_four_values(self):
        values = {m.value for m in ValidationStatus}
        assert values == {"passed", "failed", "skipped", "error"}


class TestRiskLevel:
    def test_ordering_values(self):
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"


class TestPriority:
    def test_round_trip(self):
        assert Priority("critical") is Priority.CRITICAL


class TestFailureReasonCategory:
    def test_unknown_present(self):
        assert FailureReasonCategory.UNKNOWN.value == "unknown"

    def test_all_categories_present(self):
        values = {m.value for m in FailureReasonCategory}
        assert "validation_failed" in values
        assert "backend_error" in values
        assert "unsupported_request" in values
        assert "timeout" in values
        assert "no_changes" in values
        assert "conflict" in values
        assert "policy_blocked" in values
