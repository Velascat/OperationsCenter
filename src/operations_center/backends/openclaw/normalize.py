# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/openclaw/normalize.py — maps OpenClawRunCapture → canonical ExecutionResult.

The normalizer is a pure function (except for the optional git diff subprocess).
It receives OpenClaw's structured capture and produces a canonical ExecutionResult.

Raw events from the capture are intentionally NOT propagated into the canonical
result. Callers who need them should retain them as BackendDetailRef entries via
the observability layer.

Changed-file evidence is resolved explicitly with three possible states:
  known     — files enumerated via git diff (authoritative)
  inferred  — files extracted from OpenClaw event stream (bounded confidence)
  unknown   — no reliable source; changed_files will be empty

The source of changed-file information is recorded in the capture's
changed_files_source field so the observability layer can represent it
accurately. Inferred or unknown changed files are never presented as authoritative.
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
from .models import OpenClawRunCapture


def normalize(
    capture: OpenClawRunCapture,
    proposal_id: str,
    decision_id: str,
    branch_name: Optional[str] = None,
    workspace_path: Optional[Path] = None,
    validation_ran: bool = False,
    validation_passed: Optional[bool] = None,
    validation_excerpt: Optional[str] = None,
    validation_duration_ms: Optional[int] = None,
) -> ExecutionResult:
    """Map an OpenClawRunCapture into a canonical ExecutionResult.

    Changed-file resolution order:
      1. git diff on workspace_path — authoritative, source="git_diff"
      2. OpenClaw-reported files from event stream — inferred, source="event_stream"
      3. None available — source="unknown", changed_files=[]

    The capture's changed_files_source is updated to reflect which path was used.
    Inferred changed files from the event stream are included in the result but
    the observability layer can inspect changed_files_source to represent the
    evidence correctly.

    Args:
        capture:               Raw OpenClaw outputs.
        proposal_id:           From the originating ExecutionRequest.
        decision_id:           From the originating ExecutionRequest.
        branch_name:           Task branch name (for tracing).
        workspace_path:        Workspace root; used first to discover changed files.
        validation_ran:        True if validation commands were executed.
        validation_passed:     True/False/None when validation ran.
        validation_excerpt:    First failure output if validation failed.
        validation_duration_ms: Validation wall-clock time.
    """
    success = capture.succeeded
    status = ExecutionStatus.SUCCEEDED if success else _map_failure_status(capture)

    changed_files, resolved_source = _resolve_changed_files(capture, workspace_path)
    diff_stat = _build_diff_stat(changed_files)

    # Mutate source on capture so observability layer sees the final state.
    # (dataclass is not frozen, intentional)
    capture.changed_files_source = resolved_source

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
        changed_files_source=resolved_source,
        changed_files_confidence=_changed_files_confidence(resolved_source),
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
# Changed-file evidence resolution
# ---------------------------------------------------------------------------

def _resolve_changed_files(
    capture: OpenClawRunCapture,
    workspace_path: Optional[Path],
) -> tuple[list[ChangedFileRef], str]:
    """Resolve changed files with explicit source tracking.

    Returns:
        (changed_files, source) where source is one of:
          "git_diff"     — authoritative, from git diff
          "event_stream" — inferred, from OpenClaw-reported files
          "unknown"      — no reliable source

    Never presents inferred or unknown files as authoritative.
    """
    if workspace_path and str(workspace_path) not in ("", "."):
        git_files = _discover_changed_files_via_git(workspace_path)
        if git_files is not None:
            return git_files, "git_diff"

    if capture.reported_changed_files:
        inferred = _parse_reported_changed_files(capture.reported_changed_files)
        if inferred:
            return inferred, "event_stream"

    return [], "unknown"


def _discover_changed_files_via_git(workspace_path: Path) -> Optional[list[ChangedFileRef]]:
    """Run git diff to discover changed files. Returns None if git is unavailable."""
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
            return None

        refs: list[ChangedFileRef] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status_char, path = parts
            change_type = _git_status_to_change_type(status_char)
            refs.append(ChangedFileRef(path=path.strip(), change_type=change_type))
        return refs
    except Exception:
        return None


def _parse_reported_changed_files(
    reported: list[dict],
) -> list[ChangedFileRef]:
    """Parse OpenClaw-reported changed files from event stream data.

    The format is a list of dicts with at minimum a "path" key.
    Optional "change_type" key maps to canonical change type.
    """
    refs: list[ChangedFileRef] = []
    for entry in reported:
        path = entry.get("path", "")
        if not path:
            continue
        change_type = entry.get("change_type", "modified")
        refs.append(ChangedFileRef(path=path, change_type=change_type))
    return refs


def _git_status_to_change_type(status: str) -> str:
    mapping = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    return mapping.get(status[:1].upper(), "modified")


# ---------------------------------------------------------------------------
# Other helpers
# ---------------------------------------------------------------------------

def _map_failure_status(capture: OpenClawRunCapture) -> ExecutionStatus:
    if capture.timeout_hit or capture.outcome == "timeout":
        return ExecutionStatus.TIMED_OUT
    return ExecutionStatus.FAILED


def _build_diff_stat(changed_files: list[ChangedFileRef]) -> Optional[str]:
    if not changed_files:
        return None
    n = len(changed_files)
    return f"{n} file{'s' if n != 1 else ''} changed"


def _changed_files_confidence(source: str) -> float:
    if source == "git_diff":
        return 1.0
    if source == "event_stream":
        return 0.5
    return 0.0


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


def _map_artifacts(capture: OpenClawRunCapture) -> list[ExecutionArtifact]:
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
