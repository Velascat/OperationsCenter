"""Shared fixture factories for observability tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionArtifact, ExecutionResult


def _ts() -> datetime:
    return datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)


def make_result(
    run_id: str = "run-0001",
    proposal_id: str = "prop-0001",
    decision_id: str = "dec-0001",
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    success: bool = True,
    changed_files: Optional[list[ChangedFileRef]] = None,
    changed_files_source: Optional[str] = None,
    changed_files_confidence: Optional[float] = None,
    diff_stat_excerpt: Optional[str] = None,
    validation_status: ValidationStatus = ValidationStatus.SKIPPED,
    validation_commands_run: int = 0,
    validation_commands_passed: int = 0,
    validation_commands_failed: int = 0,
    validation_failure_excerpt: Optional[str] = None,
    failure_category: Optional[FailureReasonCategory] = None,
    failure_reason: Optional[str] = None,
    artifacts: Optional[list[ExecutionArtifact]] = None,
    branch_name: Optional[str] = "auto/lint-fix-abc123",
) -> ExecutionResult:
    return ExecutionResult(
        run_id=run_id,
        proposal_id=proposal_id,
        decision_id=decision_id,
        status=status,
        success=success,
        changed_files=changed_files or [],
        changed_files_source=changed_files_source,
        changed_files_confidence=changed_files_confidence,
        diff_stat_excerpt=diff_stat_excerpt,
        validation=ValidationSummary(
            status=validation_status,
            commands_run=validation_commands_run,
            commands_passed=validation_commands_passed,
            commands_failed=validation_commands_failed,
            failure_excerpt=validation_failure_excerpt,
        ),
        failure_category=failure_category,
        failure_reason=failure_reason,
        artifacts=artifacts or [],
        branch_name=branch_name,
    )


def make_artifact(
    artifact_type: ArtifactType = ArtifactType.LOG_EXCERPT,
    label: str = "test artifact",
    content: str = "artifact content",
) -> ExecutionArtifact:
    return ExecutionArtifact(
        artifact_type=artifact_type,
        label=label,
        content=content,
    )


def make_changed_file(path: str = "src/main.py", change_type: str = "modified") -> ChangedFileRef:
    return ChangedFileRef(path=path, change_type=change_type)


# ---------------------------------------------------------------------------
# Named scenario fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def successful_rich_result() -> ExecutionResult:
    """Successful run: known changed files, validation passed, diff artifact."""
    return make_result(
        run_id="run-rich-01",
        status=ExecutionStatus.SUCCESS,
        success=True,
        changed_files=[
            make_changed_file("src/main.py", "modified"),
            make_changed_file("src/utils.py", "modified"),
        ],
        changed_files_source="git_diff",
        changed_files_confidence=1.0,
        diff_stat_excerpt="2 files changed, 14 insertions(+), 3 deletions(-)",
        validation_status=ValidationStatus.PASSED,
        validation_commands_run=2,
        validation_commands_passed=2,
        artifacts=[
            make_artifact(ArtifactType.DIFF, "pre-merge diff", "--- a/src/main.py\n+++ b/src/main.py"),
            make_artifact(ArtifactType.VALIDATION_REPORT, "ruff output", "All checks passed."),
            make_artifact(ArtifactType.LOG_EXCERPT, "kodo run log", "kodo: 2 files fixed\nkodo: done"),
        ],
    )


@pytest.fixture
def failed_result_with_logs() -> ExecutionResult:
    """Failed run: no changed files, backend error, log artifact only."""
    return make_result(
        run_id="run-fail-01",
        status=ExecutionStatus.FAILED,
        success=False,
        changed_files=[],
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        failure_reason="kodo exited 1: tool call failed",
        artifacts=[
            make_artifact(ArtifactType.LOG_EXCERPT, "kodo run log", "kodo: exit 1"),
        ],
    )


@pytest.fixture
def timeout_result() -> ExecutionResult:
    """Timeout: no artifacts, no changed files."""
    return make_result(
        run_id="run-timeout-01",
        status=ExecutionStatus.TIMEOUT,
        success=False,
        failure_category=FailureReasonCategory.TIMEOUT,
        failure_reason="kodo exited -1: [timeout: process group killed after 300s]",
        artifacts=[],
    )


@pytest.fixture
def no_changes_result() -> ExecutionResult:
    """Backend confirmed no changes: NO_CHANGES category."""
    return make_result(
        run_id="run-nochange-01",
        status=ExecutionStatus.FAILED,
        success=False,
        changed_files=[],
        failure_category=FailureReasonCategory.NO_CHANGES,
        failure_reason="kodo: no changes detected",
        artifacts=[
            make_artifact(ArtifactType.LOG_EXCERPT, "kodo run log", "kodo: no changes"),
        ],
    )


@pytest.fixture
def policy_blocked_result() -> ExecutionResult:
    """Policy-blocked: execution never ran."""
    return make_result(
        run_id="run-blocked-01",
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.POLICY_BLOCKED,
        failure_reason="execution blocked by policy: repository requires human review",
        artifacts=[],
    )


@pytest.fixture
def sparse_result() -> ExecutionResult:
    """Minimal result: no artifacts, no changed files, plain failure."""
    return make_result(
        run_id="run-sparse-01",
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.UNKNOWN,
        artifacts=[],
    )
