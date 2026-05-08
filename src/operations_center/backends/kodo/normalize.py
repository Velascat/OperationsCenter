# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/kodo/normalize.py — maps KodoRunCapture → canonical ExecutionResult.

The normalizer is a pure function. It receives kodo's structured capture and
produces a canonical ExecutionResult. It does not call kodo, touch the filesystem,
or mutate the input.

Partial richness is expected and acceptable:
- changed file references are not available from kodo output alone;
  they are omitted when the workspace diff cannot be read
- validation output is omitted when no validation commands ran
- artifacts are preserved from the invoker's extraction pass
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from operations_center.contracts.execution import ExecutionArtifact, ExecutionResult
from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)

from .errors import build_failure_reason, categorize_failure
from .models import KodoFailureInfo, KodoRunCapture


def normalize(
    capture: KodoRunCapture,
    proposal_id: str,
    decision_id: str,
    branch_name: Optional[str] = None,
    workspace_path: Optional[Path] = None,
    validation_ran: bool = False,
    validation_passed: Optional[bool] = None,
    validation_excerpt: Optional[str] = None,
    validation_duration_ms: Optional[int] = None,
) -> ExecutionResult:
    """Map a KodoRunCapture into a canonical ExecutionResult.

    Args:
        capture:              Raw kodo run outputs.
        proposal_id:          From the originating ExecutionRequest.
        decision_id:          From the originating ExecutionRequest.
        branch_name:          Task branch name (for tracing).
        workspace_path:       Workspace root; used to discover changed files.
        validation_ran:       True if validation commands were executed.
        validation_passed:    True/False/None when validation ran.
        validation_excerpt:   First failure output if validation failed.
        validation_duration_ms: Validation wall-clock time.
    """
    # G-003 (2026-05-05): Kodo can return exit_code=0 even when its
    # internal stage execution crashed (e.g. "Done: 0/1 stage completed,
    # Stage X crashed"). Trust capture.succeeded only when stdout is
    # also free of the documented failure markers.
    stdout_failure = _scan_stdout_for_internal_failure(capture.stdout or "")
    # G-V04 / G-005 — capacity-exhaustion masquerading as success.
    capacity_excerpt = (
        _scan_for_capacity_exhaustion(capture.combined_output)
        if capture.succeeded else None
    )
    if capture.succeeded and (stdout_failure is not None or capacity_excerpt is not None):
        status = ExecutionStatus.FAILED
        success = False
    else:
        status = ExecutionStatus.SUCCEEDED if capture.succeeded else _map_failure_status(capture)
        success = capture.succeeded

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

    failure_info = _extract_failure_info(capture) if not success else None
    failure_category = FailureReasonCategory(failure_info.failure_category_value) if failure_info else None
    failure_reason = failure_info.failure_reason if failure_info else None

    # G-003: when we flipped status to FAILED via stdout scan but the
    # underlying capture exited 0, _extract_failure_info short-circuits
    # to None. Surface the stdout-derived reason so audit + recovery
    # have something to act on.
    if not success and failure_reason is None and stdout_failure is not None:
        failure_reason = stdout_failure
        failure_category = FailureReasonCategory.UNKNOWN

    # G-V04: capacity-exhaustion always wins as the failure reason because
    # it explains why the run looked successful; surface it explicitly.
    if not success and capacity_excerpt is not None:
        failure_reason = capacity_excerpt
        failure_category = FailureReasonCategory.BACKEND_ERROR

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
        branch_pushed=False,  # branch push is a lane-runner concern, not adapter concern
        branch_name=branch_name,
        pull_request_url=None,
        failure_category=failure_category,
        failure_reason=failure_reason,
        artifacts=artifacts,
        runtime_invocation_ref=getattr(capture, "invocation_ref", None),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# G-003 (2026-05-05): patterns that indicate Kodo's internal stage
# execution failed even when exit_code=0. Discovered during the real
# R6 run where ClaudeCodeOrchestrator crashed mid-stage.
import re as _re  # noqa: E402  -- intentional late import after module docstring

_INTERNAL_FAILURE_PATTERNS = (
    _re.compile(r"Done:\s*0/\d+\s+stage(?:s)?\s+completed", _re.IGNORECASE),
    _re.compile(r"\bStage\s+\d+\s+\([^)]*\)\s+crashed", _re.IGNORECASE),
    _re.compile(r"\bcrashed:\s+\S", _re.IGNORECASE),
    _re.compile(r"\bStopping run\b", _re.IGNORECASE),
)


def _scan_for_capacity_exhaustion(combined_output: str) -> Optional[str]:
    """Local alias for the shared capacity-exhaustion classifier."""
    from operations_center.backends._capacity_classifier import classify_capacity_exhaustion
    return classify_capacity_exhaustion(combined_output)


def _scan_stdout_for_internal_failure(stdout: str) -> Optional[str]:
    """Return the matched-line excerpt if stdout signals an internal stage failure."""
    if not stdout:
        return None
    for pattern in _INTERNAL_FAILURE_PATTERNS:
        match = pattern.search(stdout)
        if match:
            line_start = stdout.rfind("\n", 0, match.start()) + 1
            line_end = stdout.find("\n", match.end())
            line = stdout[line_start:line_end if line_end != -1 else None].strip()
            return f"internal stage failure: {line[:160]}"
    return None


def _extract_failure_info(capture: KodoRunCapture) -> KodoFailureInfo | None:
    if capture.exit_code == 0 and capture.succeeded:
        return None
    return KodoFailureInfo(
        exit_code=capture.exit_code,
        failure_category_value=categorize_failure(capture.exit_code, capture.combined_output).value,
        failure_reason=build_failure_reason(capture.exit_code, capture.stderr, capture.stdout),
        is_timeout=capture.timeout_hit,
    )


def _map_failure_status(capture: KodoRunCapture) -> ExecutionStatus:
    if capture.timeout_hit:
        return ExecutionStatus.TIMED_OUT
    return ExecutionStatus.FAILED


def _discover_changed_files(workspace_path: Path) -> tuple[list[ChangedFileRef], str, float]:
    """Read git diff --name-status to discover changed files.

    Returns an empty list if the workspace is not a git repo or git is
    unavailable. Missing changed-file data is normal; callers must not
    assume this list is always populated.
    """
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


def _map_artifacts(capture: KodoRunCapture) -> list[ExecutionArtifact]:
    """Convert KodoArtifactCapture items into canonical ExecutionArtifact."""
    from operations_center.contracts.execution import ExecutionArtifact

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
