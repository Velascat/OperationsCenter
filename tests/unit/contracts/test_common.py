# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for shared value objects (common.py)."""

from __future__ import annotations

import pytest

from operations_center.contracts.common import (
    BranchPolicy,
    ChangedFileRef,
    ExecutionConstraints,
    TaskTarget,
    ValidationProfile,
    ValidationSummary,
)
from operations_center.contracts.enums import ValidationStatus


class TestTaskTarget:
    def test_minimal_construction(self):
        t = TaskTarget(
            repo_key="my-service",
            clone_url="https://github.com/org/repo.git",
            base_branch="main",
        )
        assert t.repo_key == "my-service"
        assert t.allowed_paths == []

    def test_with_allowed_paths(self):
        t = TaskTarget(
            repo_key="r",
            clone_url="https://example.com/r.git",
            base_branch="main",
            allowed_paths=["src/**", "tests/**"],
        )
        assert len(t.allowed_paths) == 2

    def test_frozen(self):
        t = TaskTarget(repo_key="r", clone_url="u", base_branch="main")
        with pytest.raises(Exception):
            t.repo_key = "other"  # type: ignore[misc]

    def test_json_round_trip(self):
        t = TaskTarget(repo_key="r", clone_url="u", base_branch="main")
        restored = TaskTarget.model_validate_json(t.model_dump_json())
        assert restored == t


class TestExecutionConstraints:
    def test_defaults(self):
        c = ExecutionConstraints()
        assert c.max_changed_files is None
        assert c.timeout_seconds == 300
        assert c.require_clean_validation is True
        assert c.skip_baseline_validation is False

    def test_custom_timeout(self):
        c = ExecutionConstraints(timeout_seconds=600)
        assert c.timeout_seconds == 600

    def test_timeout_must_be_positive(self):
        with pytest.raises(Exception):
            ExecutionConstraints(timeout_seconds=0)


class TestValidationProfile:
    def test_minimal(self):
        p = ValidationProfile(profile_name="off")
        assert p.commands == []
        assert p.fail_fast is False

    def test_with_commands(self):
        p = ValidationProfile(profile_name="strict", commands=["ruff check .", "pytest"])
        assert len(p.commands) == 2

    def test_frozen(self):
        p = ValidationProfile(profile_name="x")
        with pytest.raises(Exception):
            p.profile_name = "y"  # type: ignore[misc]


class TestBranchPolicy:
    def test_defaults(self):
        bp = BranchPolicy()
        assert bp.branch_prefix == "auto/"
        assert bp.push_on_success is True
        assert bp.open_pr is False
        assert bp.allowed_base_branches == []

    def test_custom(self):
        bp = BranchPolicy(branch_prefix="fix/", open_pr=True)
        assert bp.branch_prefix == "fix/"
        assert bp.open_pr is True


class TestChangedFileRef:
    def test_minimal(self):
        ref = ChangedFileRef(path="src/main.py")
        assert ref.change_type == "modified"
        assert ref.lines_added is None

    def test_with_stats(self):
        ref = ChangedFileRef(path="src/main.py", change_type="added", lines_added=50, lines_removed=0)
        assert ref.lines_added == 50

    def test_frozen(self):
        ref = ChangedFileRef(path="x.py")
        with pytest.raises(Exception):
            ref.path = "y.py"  # type: ignore[misc]


class TestValidationSummary:
    def test_passed(self):
        s = ValidationSummary(
            status=ValidationStatus.PASSED,
            commands_run=3,
            commands_passed=3,
            commands_failed=0,
        )
        assert s.status == ValidationStatus.PASSED
        assert s.failure_excerpt is None

    def test_failed_with_excerpt(self):
        s = ValidationSummary(
            status=ValidationStatus.FAILED,
            commands_run=2,
            commands_passed=1,
            commands_failed=1,
            failure_excerpt="AssertionError: 1 != 2",
        )
        assert s.failure_excerpt == "AssertionError: 1 != 2"

    def test_skipped(self):
        s = ValidationSummary(status=ValidationStatus.SKIPPED)
        assert s.commands_run == 0

    def test_json_round_trip(self):
        s = ValidationSummary(status=ValidationStatus.PASSED, commands_run=1, commands_passed=1)
        restored = ValidationSummary.model_validate_json(s.model_dump_json())
        assert restored == s
