# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/aider_local/adapter.py — AiderLocalBackendAdapter.

Runs Aider CLI backed by a local Ollama inference server.
Uses --model ollama/<model> + --api-base <ollama_base_url> so no cloud API key
is needed. Goal text is written to a temp file and passed via --message-file to
handle long prompts without shell escaping issues.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from executor_runtime import ExecutorRuntime
from rxp.contracts import RuntimeInvocation

from operations_center.config.settings import AiderLocalSettings
from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionArtifact, ExecutionRequest, ExecutionResult


class AiderLocalBackendAdapter:
    """Canonical adapter for the aider_local CPU execution backend.

    Phase 2 + 3 of the OC runtime extraction: subprocess invocation
    is delegated to ``ExecutorRuntime`` (subprocess kind). Same pattern
    as kodo and direct_local.
    """

    def __init__(
        self,
        settings: AiderLocalSettings,
        runtime: ExecutorRuntime | None = None,
    ) -> None:
        self._runtime = runtime or ExecutorRuntime()
        self._settings = settings

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        repo_path = Path(request.workspace_path)

        message = request.goal_text
        if request.constraints_text:
            message = f"{request.goal_text}\n\n## Constraints\n{request.constraints_text}"

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix=f"aider_local_{request.run_id}_",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(message)
            message_file = tmp.name

        try:
            command = self._build_command(message_file)
            run_result = self._run(command=command, repo_path=repo_path)
        finally:
            try:
                os.unlink(message_file)
            except OSError:
                pass

        changed_files, changed_files_source, changed_files_confidence = _discover_changed_files(repo_path)
        failure_category = _failure_category(run_result)
        failure_reason = None if run_result.success else (run_result.output or "aider_local execution failed")

        artifacts: list[ExecutionArtifact] = []
        if run_result.output:
            artifacts.append(
                ExecutionArtifact(
                    artifact_type=ArtifactType.LOG_EXCERPT,
                    label="aider_local run log",
                    content=_truncate(run_result.output, 4000),
                )
            )

        return ExecutionResult(
            run_id=request.run_id,
            proposal_id=request.proposal_id,
            decision_id=request.decision_id,
            status=ExecutionStatus.SUCCEEDED if run_result.success else _failure_status(run_result),
            success=run_result.success,
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

    def _build_command(self, message_file: str) -> list[str]:
        command = [
            self._settings.binary,
            "--model", self._settings.model,
            "--api-base", self._settings.ollama_base_url,
            "--yes-always",
            "--message-file", message_file,
        ]
        command += self._settings.extra_args
        return command

    def _run(self, *, command: list[str], repo_path: Path) -> "_AiderLocalRunResult":
        env = os.environ.copy()
        # Ollama does not use OPENAI_API_KEY; set a dummy to avoid aider warnings
        if not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = "sk-local-ollama"

        artifact_dir = tempfile.mkdtemp(prefix="aider-local-")

        invocation = RuntimeInvocation(
            invocation_id=_short_id(),
            runtime_name="aider_local",
            runtime_kind="subprocess",
            working_directory=str(repo_path),
            command=list(command),
            environment=dict(env),
            timeout_seconds=self._settings.timeout_seconds
            if self._settings.timeout_seconds and self._settings.timeout_seconds > 0
            else None,
            artifact_directory=artifact_dir,
        )

        try:
            rxp_result = self._runtime.run(invocation)
        except FileNotFoundError:
            return _AiderLocalRunResult(
                success=False,
                output=f"[aider_local] Binary not found: {self._settings.binary}",
                metadata={"command": command, "binary_missing": True},
            )

        if rxp_result.status == "rejected":
            return _AiderLocalRunResult(
                success=False,
                output=rxp_result.error_summary or "executor runtime rejected invocation",
                metadata={"command": command},
            )
        if rxp_result.status == "timed_out":
            return _AiderLocalRunResult(
                success=False,
                output=f"[aider_local] Timed out after {self._settings.timeout_seconds}s",
                metadata={"command": command, "timeout_hit": True},
            )

        stdout = _read_capture(rxp_result.stdout_path)
        stderr = _read_capture(rxp_result.stderr_path)
        output = (stdout + stderr).strip()
        return _AiderLocalRunResult(
            success=rxp_result.status == "succeeded",
            output=output,
            exit_code=rxp_result.exit_code,
            metadata={"command": command, "model": self._settings.model},
        )


class _AiderLocalRunResult:
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


def _read_capture(path: str | None) -> str:
    """Read a captured stdout/stderr file produced by ExecutorRuntime."""
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _short_id() -> str:
    """Cheap unique-enough invocation id for aider_local runs."""
    import uuid
    return f"aider-local-{uuid.uuid4().hex[:8]}"


def _failure_status(result: _AiderLocalRunResult) -> ExecutionStatus:
    if result.metadata.get("timeout_hit"):
        return ExecutionStatus.TIMED_OUT
    return ExecutionStatus.FAILED


def _failure_category(result: _AiderLocalRunResult) -> Optional[FailureReasonCategory]:
    if result.success:
        return None
    if result.metadata.get("timeout_hit"):
        return FailureReasonCategory.TIMEOUT
    if result.metadata.get("binary_missing"):
        return FailureReasonCategory.BACKEND_ERROR
    return FailureReasonCategory.BACKEND_ERROR


def _discover_changed_files(workspace_path: Path) -> tuple[list[ChangedFileRef], str, float]:
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
