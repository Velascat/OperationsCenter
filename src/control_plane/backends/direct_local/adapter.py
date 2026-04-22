"""
backends/direct_local/adapter.py — DirectLocalBackendAdapter.

This adapter wraps the existing Aider CLI executor behind the canonical
ExecutionRequest -> ExecutionResult contract for the local execution lane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from control_plane.adapters.executor.aider import AiderAdapter
from control_plane.adapters.executor.protocol import ExecutorTask
from control_plane.config.settings import AiderSettings
from control_plane.contracts.common import ChangedFileRef, ValidationSummary
from control_plane.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from control_plane.contracts.execution import ExecutionArtifact, ExecutionRequest, ExecutionResult


class DirectLocalBackendAdapter:
    """Canonical adapter for the local direct execution backend."""

    def __init__(self, settings: AiderSettings, switchboard_url: str = "") -> None:
        self._executor = AiderAdapter(settings, switchboard_url=switchboard_url)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        result = self._executor.execute(
            ExecutorTask(
                goal=request.goal_text,
                repo_path=Path(request.workspace_path),
                constraints=request.constraints_text or "",
            )
        )

        changed_files = _discover_changed_files(Path(request.workspace_path))
        failure_category = _failure_category(result)
        failure_reason = None if result.success else (result.output or "direct_local execution failed")

        artifacts: list[ExecutionArtifact] = []
        if result.output:
            artifacts.append(
                ExecutionArtifact(
                    artifact_type=ArtifactType.LOG_EXCERPT,
                    label="direct_local run log",
                    content=_truncate(result.output, 4000),
                )
            )

        return ExecutionResult(
            run_id=request.run_id,
            proposal_id=request.proposal_id,
            decision_id=request.decision_id,
            status=ExecutionStatus.SUCCESS if result.success else _failure_status(result),
            success=result.success,
            changed_files=changed_files,
            diff_stat_excerpt=_diff_stat(changed_files),
            validation=ValidationSummary(status=ValidationStatus.SKIPPED),
            branch_pushed=False,
            branch_name=request.task_branch,
            failure_category=failure_category,
            failure_reason=failure_reason,
            artifacts=artifacts,
        )


def _failure_status(result) -> ExecutionStatus:
    if result.metadata.get("timeout_hit"):
        return ExecutionStatus.TIMEOUT
    return ExecutionStatus.FAILED


def _failure_category(result) -> Optional[FailureReasonCategory]:
    if result.success:
        return None
    if result.metadata.get("timeout_hit"):
        return FailureReasonCategory.TIMEOUT
    return FailureReasonCategory.BACKEND_ERROR


def _discover_changed_files(workspace_path: Path) -> list[ChangedFileRef]:
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "diff", "--name-status", "HEAD"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return []

    if proc.returncode != 0:
        return []

    refs: list[ChangedFileRef] = []
    for line in proc.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status_char, path = parts
        refs.append(
            ChangedFileRef(
                path=path.strip(),
                change_type=_git_status_to_change_type(status_char),
            )
        )
    return refs


def _git_status_to_change_type(status: str) -> str:
    mapping = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    return mapping.get(status[:1].upper(), "modified")


def _diff_stat(changed_files: list[ChangedFileRef]) -> str | None:
    if not changed_files:
        return None
    count = len(changed_files)
    return f"{count} file{'s' if count != 1 else ''} changed"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]
