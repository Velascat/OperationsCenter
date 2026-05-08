# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/direct_local/adapter.py — DirectLocalBackendAdapter.

This adapter runs the Aider CLI behind the canonical
ExecutionRequest -> ExecutionResult contract for the local execution lane.

Phase 2 + 3 of the OC runtime extraction: subprocess invocation is
delegated to ``ExecutorRuntime`` (subprocess kind). The adapter
constructs an RxP ``RuntimeInvocation``, hands it to
``ExecutorRuntime.run``, reads stdout/stderr from the paths in the
returned ``RuntimeResult``, and assembles the existing
``_DirectLocalRunResult`` for the adapter's downstream code.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from executor_runtime import ExecutorRuntime
from rxp.contracts import RuntimeInvocation

from operations_center.config.settings import AiderSettings
from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionArtifact, ExecutionRequest, ExecutionResult
from operations_center.backends._capacity_classifier import classify_capacity_exhaustion
from operations_center.backends._runtime_ref import runtime_invocation_ref


class DirectLocalBackendAdapter:
    """Canonical adapter for the local direct execution backend."""

    def __init__(
        self,
        settings: AiderSettings,
        runtime: ExecutorRuntime | None = None,
    ) -> None:
        self._settings = settings
        self._runtime = runtime or ExecutorRuntime()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        repo_path = Path(request.workspace_path)
        command, env = self._build_invocation(
            _repo_path=repo_path,
            goal=request.goal_text,
            constraints=request.constraints_text or "",
        )
        result = self._run(command=command, repo_path=repo_path, env=env)

        # G-V04 — guard against false success when the upstream backend
        # printed a capacity-exhaustion notice and exited 0.
        if result.success:
            capacity_excerpt = classify_capacity_exhaustion(result.output)
            if capacity_excerpt is not None:
                result.success = False
                result.metadata["capacity_exhausted"] = True
                result.output = capacity_excerpt

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
            runtime_invocation_ref=result.invocation_ref,
        )

    def _build_invocation(
        self,
        *,
        _repo_path: Path,
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
        # Per-call artifact dir so ER's stdout/stderr capture doesn't
        # pollute the workspace.
        artifact_dir = tempfile.mkdtemp(prefix="direct-local-")

        invocation = RuntimeInvocation(
            invocation_id=_short_id(),
            runtime_name="direct_local",
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
            return _DirectLocalRunResult(
                success=False,
                output=f"[aider] Binary not found: {self._settings.binary}",
                metadata={"command": command},
                invocation_ref=runtime_invocation_ref(invocation),
            )

        ref = runtime_invocation_ref(invocation, rxp_result)
        if rxp_result.status == "rejected":
            return _DirectLocalRunResult(
                success=False,
                output=rxp_result.error_summary or "executor runtime rejected invocation",
                metadata={"command": command},
                invocation_ref=ref,
            )
        if rxp_result.status == "timed_out":
            return _DirectLocalRunResult(
                success=False,
                output=f"[aider] Timed out after {self._settings.timeout_seconds}s",
                metadata={"command": command, "timeout_hit": True},
                invocation_ref=ref,
            )

        stdout = _read_capture(rxp_result.stdout_path)
        stderr = _read_capture(rxp_result.stderr_path)
        output = (stdout + stderr).strip()
        return _DirectLocalRunResult(
            success=rxp_result.status == "succeeded",
            output=output,
            exit_code=rxp_result.exit_code,
            metadata={"command": command, "model": command[2]},
            invocation_ref=ref,
        )


class _DirectLocalRunResult:
    def __init__(
        self,
        *,
        success: bool,
        output: str,
        exit_code: int | None = None,
        metadata: dict[str, object] | None = None,
        invocation_ref=None,
    ) -> None:
        self.success = success
        self.output = output
        self.exit_code = exit_code
        self.metadata = metadata or {}
        self.invocation_ref = invocation_ref


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
    """Cheap unique-enough invocation id for direct_local runs."""
    import uuid
    return f"direct-local-{uuid.uuid4().hex[:8]}"


def _failure_status(result) -> ExecutionStatus:
    if result.metadata.get("timeout_hit"):
        return ExecutionStatus.TIMED_OUT
    return ExecutionStatus.FAILED


def _failure_category(result) -> Optional[FailureReasonCategory]:
    if result.success:
        return None
    if result.metadata.get("timeout_hit"):
        return FailureReasonCategory.TIMEOUT
    if result.metadata.get("capacity_exhausted"):
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
