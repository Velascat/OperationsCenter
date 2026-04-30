# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/direct_local/adapter.py — DirectLocalBackendAdapter.

This adapter runs the Aider CLI behind the canonical
ExecutionRequest -> ExecutionResult contract for the local execution lane.
"""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
from typing import Optional

from operations_center.config.settings import AiderSettings
from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionArtifact, ExecutionRequest, ExecutionResult


class DirectLocalBackendAdapter:
    """Canonical adapter for the local direct execution backend."""

    def __init__(self, settings: AiderSettings) -> None:
        self._settings = settings

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        repo_path = Path(request.workspace_path)
        command, env = self._build_invocation(
            repo_path=repo_path,
            goal=request.goal_text,
            constraints=request.constraints_text or "",
        )
        result = self._run(command=command, repo_path=repo_path, env=env)

        changed_files, changed_files_source, changed_files_confidence = _discover_changed_files(repo_path)
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
            status=ExecutionStatus.SUCCEEDED if result.success else _failure_status(result),
            success=result.success,
            changed_files=changed_files,
            changed_files_source=changed_files_source,
            changed_files_confidence=changed_files_confidence,
            diff_stat_excerpt=_diff_stat(changed_files),
            validation=ValidationSummary(status=ValidationStatus.SKIPPED),
            branch_pushed=False,
            branch_name=request.task_branch,
            failure_category=failure_category,
            failure_reason=failure_reason,
            artifacts=artifacts,
        )

    def _build_invocation(
        self,
        *,
        repo_path: Path,
        goal: str,
        constraints: str,
    ) -> tuple[list[str], dict[str, str]]:
        model = f"{self._settings.model_prefix}/{self._settings.profile}"
        message = goal
        if constraints:
            message = f"{goal}\n\n## Constraints\n{constraints}"

        command = [
            self._settings.binary,
            "--model",
            model,
            "--message",
            message,
            "--yes",
        ]
        if self._settings.model_settings_file:
            model_settings = Path(self._settings.model_settings_file)
            if model_settings.exists():
                command += ["--model-settings-file", str(model_settings)]
        command += self._settings.extra_args

        env = os.environ.copy()
        if not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = "sk-local-direct"
        return command, env

    def _run(self, *, command: list[str], repo_path: Path, env: dict[str, str]) -> "_DirectLocalRunResult":
        try:
            proc = subprocess.run(
                command,
                cwd=repo_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=self._settings.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return _DirectLocalRunResult(
                success=False,
                output=f"[aider] Timed out after {self._settings.timeout_seconds}s",
                metadata={"command": command, "timeout_hit": True},
            )
        except FileNotFoundError:
            return _DirectLocalRunResult(
                success=False,
                output=f"[aider] Binary not found: {self._settings.binary}",
                metadata={"command": command},
            )

        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return _DirectLocalRunResult(
            success=proc.returncode == 0,
            output=output,
            exit_code=proc.returncode,
            metadata={"command": command, "model": command[2]},
        )


class _DirectLocalRunResult:
    def __init__(
        self,
        *,
        success: bool,
        output: str,
        exit_code: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.success = success
        self.output = output
        self.exit_code = exit_code
        self.metadata = metadata or {}


def _failure_status(result) -> ExecutionStatus:
    if result.metadata.get("timeout_hit"):
        return ExecutionStatus.TIMED_OUT
    return ExecutionStatus.FAILED


def _failure_category(result) -> Optional[FailureReasonCategory]:
    if result.success:
        return None
    if result.metadata.get("timeout_hit"):
        return FailureReasonCategory.TIMEOUT
    return FailureReasonCategory.BACKEND_ERROR


def _discover_changed_files(workspace_path: Path) -> tuple[list[ChangedFileRef], str, float]:
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
        return [], "unknown", 0.0

    if proc.returncode != 0:
        return [], "unknown", 0.0

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
    return refs, "git_diff", 1.0


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
