"""
Shared fixtures for tuning unit tests.

Provides factory functions for building ExecutionRecord objects with
controlled characteristics for routing strategy analysis tests.
"""

from __future__ import annotations


from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionResult
from operations_center.observability.recorder import ExecutionRecorder
from operations_center.observability.models import ExecutionRecord


_recorder = ExecutionRecorder()


def make_result(
    *,
    run_id: str = "run-0001",
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED,
    success: bool = True,
    failure_category: FailureReasonCategory | None = None,
    validation_status: ValidationStatus = ValidationStatus.SKIPPED,
    changed_files: list[ChangedFileRef] | None = None,
    changed_files_source: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        run_id=run_id,
        proposal_id="prop-test",
        decision_id="dec-test",
        status=status,
        success=success,
        changed_files=changed_files or [],
        changed_files_source=changed_files_source,
        failure_category=failure_category,
        validation=ValidationSummary(
            status=validation_status,
            commands_run=1 if validation_status != ValidationStatus.SKIPPED else 0,
            commands_passed=1 if validation_status == ValidationStatus.PASSED else 0,
            commands_failed=1 if validation_status == ValidationStatus.FAILED else 0,
        ),
    )


def make_record(
    *,
    run_id: str = "run-0001",
    backend: str = "kodo",
    lane: str = "claude_cli",
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED,
    success: bool = True,
    failure_category: FailureReasonCategory | None = None,
    validation_status: ValidationStatus = ValidationStatus.SKIPPED,
    changed_files: list[ChangedFileRef] | None = None,
    changed_files_source: str = "backend_manifest",
    duration_ms: int | None = None,
    task_type: str | None = None,
    risk_level: str | None = None,
) -> ExecutionRecord:
    """Build an ExecutionRecord with controlled characteristics."""
    result = make_result(
        run_id=run_id,
        status=status,
        success=success,
        failure_category=failure_category,
        validation_status=validation_status,
        changed_files_source=changed_files_source,
        changed_files=changed_files or (
            [ChangedFileRef(path="src/main.py", change_type="modified")]
            if success and changed_files is None
            else []
        ),
    )
    metadata: dict[str, str] = {}
    if duration_ms is not None:
        metadata["duration_ms"] = str(duration_ms)
    if task_type is not None:
        metadata["task_type"] = task_type
    if risk_level is not None:
        metadata["risk_level"] = risk_level

    return _recorder.record(
        result,
        backend=backend,
        lane=lane,
        metadata=metadata,
    )


def make_success(backend: str = "kodo", lane: str = "claude_cli", **kw) -> ExecutionRecord:
    return make_record(
        backend=backend,
        lane=lane,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        **kw,
    )


def make_failure(backend: str = "kodo", lane: str = "claude_cli", **kw) -> ExecutionRecord:
    return make_record(
        backend=backend,
        lane=lane,
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.BACKEND_ERROR,
        changed_files=[],
        **kw,
    )


def make_timeout(backend: str = "kodo", lane: str = "claude_cli", **kw) -> ExecutionRecord:
    return make_record(
        backend=backend,
        lane=lane,
        status=ExecutionStatus.TIMED_OUT,
        success=False,
        failure_category=FailureReasonCategory.TIMEOUT,
        changed_files=[],
        **kw,
    )


def make_no_changes(backend: str = "kodo", lane: str = "claude_cli", **kw) -> ExecutionRecord:
    return make_record(
        backend=backend,
        lane=lane,
        status=ExecutionStatus.FAILED,
        success=False,
        failure_category=FailureReasonCategory.NO_CHANGES,
        changed_files=[],
        **kw,
    )


def make_unknown_changed_files(
    backend: str = "kodo", lane: str = "claude_cli", **kw
) -> ExecutionRecord:
    """Successful run but no changed-file info (backend didn't report)."""
    result = make_result(
        run_id=kw.pop("run_id", "run-0001"),
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        changed_files=[],  # empty → normalize_changed_files returns UNKNOWN
    )
    metadata: dict[str, str] = {}
    if "duration_ms" in kw:
        metadata["duration_ms"] = str(kw.pop("duration_ms"))
    return _recorder.record(result, backend=backend, lane=lane, metadata=metadata)


def make_n_successes(
    n: int,
    backend: str = "kodo",
    lane: str = "claude_cli",
    **kw,
) -> list[ExecutionRecord]:
    return [make_success(backend=backend, lane=lane, run_id=f"run-s-{i:04d}", **kw) for i in range(n)]


def make_n_failures(
    n: int,
    backend: str = "kodo",
    lane: str = "claude_cli",
    **kw,
) -> list[ExecutionRecord]:
    return [make_failure(backend=backend, lane=lane, run_id=f"run-f-{i:04d}", **kw) for i in range(n)]
