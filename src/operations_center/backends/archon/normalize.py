"""
backends/archon/normalize.py — maps ArchonRunCapture → canonical ExecutionResult.

The normalizer is a pure function. It receives Archon's structured capture and
produces a canonical ExecutionResult. It does not call Archon, touch the
filesystem, or mutate the input.

Raw workflow_events from the capture are intentionally NOT propagated into the
canonical result. Callers who need them should retain them as BackendDetailRef
entries via the observability layer.

Partial richness is expected and acceptable:
- changed files are discovered via git diff when workspace_path is provided;
  omitted when unavailable
- validation output is omitted when no validation commands ran
- workflow_events are present in ArchonRunCapture but not in ExecutionResult
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionArtifact, ExecutionResult

from .errors import build_failure_reason, categorize_failure
from .models import ArchonRunCapture


def normalize(
    capture: ArchonRunCapture,
    proposal_id: str,
    decision_id: str,
    branch_name: Optional[str] = None,
    workspace_path: Optional[Path] = None,
    validation_ran: bool = False,
    validation_passed: Optional[bool] = None,
    validation_excerpt: Optional[str] = None,
    validation_duration_ms: Optional[int] = None,
) -> ExecutionResult:
    """Map an ArchonRunCapture into a canonical ExecutionResult.

    Args:
        capture:               Raw Archon workflow outputs.
        proposal_id:           From the originating ExecutionRequest.
        decision_id:           From the originating ExecutionRequest.
        branch_name:           Task branch name (for tracing).
        workspace_path:        Workspace root; used to discover changed files.
        validation_ran:        True if validation commands were executed.
        validation_passed:     True/False/None when validation ran.
        validation_excerpt:    First failure output if validation failed.
        validation_duration_ms: Validation wall-clock time.
    """
    success = capture.succeeded
    status = ExecutionStatus.SUCCEEDED if success else _map_failure_status(capture)

    changed_files, changed_files_source, changed_files_confidence = (
        _discover_changed_files(workspace_path) if workspace_path else ([], "unknown", 0.0)
    )
    diff_stat = _build_diff_stat(changed_files)

    validation = _build_validation_summary(
        ran=validation_ran,
        passed=validation_passed,
        excerpt=validation_excerpt,
        duration_ms=validation_duration_ms,
    )

    artifacts = _map_artifacts(capture)

    failure_category: Optional[FailureReasonCategory] = None
    failure_reason: Optional[str] = None
    if not success:
        failure_category = categorize_failure(capture.outcome, capture.combined_output)
        failure_reason = build_failure_reason(
            capture.outcome, capture.error_text, capture.output_text
        )

    return ExecutionResult(
        run_id=capture.run_id,
        proposal_id=proposal_id,
        decision_id=decision_id,
        status=status,
        success=success,
        changed_files=changed_files,
        changed_files_source=changed_files_source,
        changed_files_confidence=changed_files_confidence,
        diff_stat_excerpt=diff_stat,
        validation=validation,
        branch_pushed=False,
        branch_name=branch_name,
        pull_request_url=None,
        failure_category=failure_category,
        failure_reason=failure_reason,
        artifacts=artifacts,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _map_failure_status(capture: ArchonRunCapture) -> ExecutionStatus:
    if capture.timeout_hit or capture.outcome == "timeout":
        return ExecutionStatus.TIMED_OUT
    return ExecutionStatus.FAILED


def _discover_changed_files(workspace_path: Path) -> tuple[list[ChangedFileRef], str, float]:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", "HEAD"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return [], "unknown", 0.0

        refs: list[ChangedFileRef] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status_char, path = parts
            change_type = _git_status_to_change_type(status_char)
            refs.append(ChangedFileRef(path=path.strip(), change_type=change_type))
        return refs, "git_diff", 1.0
    except Exception:
        return [], "unknown", 0.0


def _git_status_to_change_type(status: str) -> str:
    mapping = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    return mapping.get(status[:1].upper(), "modified")


def _build_diff_stat(changed_files: list[ChangedFileRef]) -> Optional[str]:
    if not changed_files:
        return None
    n = len(changed_files)
    return f"{n} file{'s' if n != 1 else ''} changed"


def _build_validation_summary(
    ran: bool,
    passed: Optional[bool],
    excerpt: Optional[str],
    duration_ms: Optional[int],
) -> ValidationSummary:
    if not ran:
        return ValidationSummary(status=ValidationStatus.SKIPPED)
    if passed is True:
        return ValidationSummary(
            status=ValidationStatus.PASSED,
            commands_run=1,
            commands_passed=1,
            commands_failed=0,
            duration_ms=duration_ms,
        )
    if passed is False:
        return ValidationSummary(
            status=ValidationStatus.FAILED,
            commands_run=1,
            commands_passed=0,
            commands_failed=1,
            failure_excerpt=excerpt,
            duration_ms=duration_ms,
        )
    return ValidationSummary(status=ValidationStatus.SKIPPED)


def _map_artifacts(capture: ArchonRunCapture) -> list[ExecutionArtifact]:
    result: list[ExecutionArtifact] = []
    for a in capture.artifacts:
        try:
            artifact_type = ArtifactType(a.artifact_type)
        except ValueError:
            artifact_type = ArtifactType.LOG_EXCERPT
        result.append(
            ExecutionArtifact(
                artifact_type=artifact_type,
                label=a.label,
                content=a.content or None,
            )
        )
    return result
