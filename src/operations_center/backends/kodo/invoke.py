# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/kodo/invoke.py — kodo invocation boundary.

KodoBackendInvoker isolates how kodo is launched. The rest of the system
does not need to know whether kodo runs as a subprocess, through a shim,
or via any other mechanism — that detail belongs here.

Phase 2 (RxP wire): the actual subprocess call is now mediated by an
RxP ``RuntimeInvocation`` → ``RuntimeResult`` round-trip. Kodo-specific
data (goal_text, validation_commands, kodo_mode) stays on the
PreparedRun/Capture envelope; the RxP types carry only the runtime
mechanics that Phase 3 will hand off to ExecutorRuntime.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends.kodo.runner import KodoAdapter, KodoRunResult

from .models import KodoArtifactCapture, KodoPreparedRun, KodoRunCapture


class KodoBackendInvoker:
    """Invokes kodo for a prepared run and returns a structured KodoRunCapture.

    Internally:
      1. writes the goal file
      2. builds an RxP ``RuntimeInvocation`` describing the subprocess call
      3. runs it via the kodo runner (Phase 3 will replace this with a
         direct ``ExecutorRuntime.run(invocation)`` call)
      4. constructs an RxP ``RuntimeResult``
      5. wraps the result + kodo-specific artifact extraction into
         ``KodoRunCapture`` for the normalizer
      6. cleans up the goal file
    """

    def __init__(self, kodo_adapter: KodoAdapter) -> None:
        self._kodo = kodo_adapter

    def invoke(self, prepared: KodoPreparedRun) -> KodoRunCapture:
        """Execute kodo for the given prepared run and return structured capture.

        Always cleans up the goal file, whether or not the run succeeds.
        """
        started_at = _now()

        self._kodo.write_goal_file(
            prepared.goal_file_path,
            prepared.goal_text,
            prepared.constraints_text,
        )

        invocation = self._build_invocation(prepared)

        try:
            rxp_result, raw = _invoke_via_rxp(invocation, self._kodo, started_at)
        finally:
            try:
                prepared.goal_file_path.unlink(missing_ok=True)
            except Exception:
                pass

        finished_at = _parse_iso(rxp_result.finished_at)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        timeout_hit = rxp_result.status == "timed_out"

        artifacts = _extract_artifacts(raw.stdout or "", raw.stderr or "")

        return KodoRunCapture(
            run_id=prepared.run_id,
            exit_code=rxp_result.exit_code if rxp_result.exit_code is not None else -1,
            stdout=raw.stdout or "",
            stderr=raw.stderr or "",
            command=list(invocation.command),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            timeout_hit=timeout_hit,
            artifacts=artifacts,
        )

    # ------------------------------------------------------------------
    # RxP wire construction
    # ------------------------------------------------------------------

    def _build_invocation(self, prepared: KodoPreparedRun) -> RuntimeInvocation:
        """Construct the RxP RuntimeInvocation for this prepared run."""
        profile = None
        if prepared.orchestrator_override is not None:
            profile = self._kodo.settings.model_copy(
                update={"orchestrator": prepared.orchestrator_override}
            )

        command = self._kodo.build_command(
            prepared.goal_file_path,
            prepared.repo_path,
            profile=profile,
            kodo_mode=prepared.kodo_mode,
        )

        env = self._build_env(prepared.env_overrides)

        timeout = prepared.timeout_seconds
        if profile is not None and profile.timeout_seconds:
            timeout = profile.timeout_seconds

        metadata: dict[str, str] = {
            "kodo_mode": prepared.kodo_mode,
            "task_branch": prepared.task_branch or "",
        }
        if prepared.orchestrator_override:
            metadata["orchestrator_override"] = prepared.orchestrator_override

        return RuntimeInvocation(
            invocation_id=prepared.run_id,
            runtime_name="kodo",
            runtime_kind="subprocess",
            working_directory=str(prepared.repo_path),
            command=command,
            environment=env,
            timeout_seconds=timeout if timeout and timeout > 0 else None,
            input_payload_path=str(prepared.goal_file_path),
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_env(self, overrides: dict[str, str]) -> dict[str, str]:
        env = os.environ.copy()
        env.update(overrides)
        return env


# ---------------------------------------------------------------------------
# RxP wire (Phase 3 replaces this with ExecutorRuntime.run(invocation))
# ---------------------------------------------------------------------------


def _invoke_via_rxp(
    invocation: RuntimeInvocation,
    runner: KodoAdapter,
    started_at: datetime,
) -> tuple[RuntimeResult, KodoRunResult]:
    """Run a RuntimeInvocation via the kodo runner. Returns the RxP
    RuntimeResult and the underlying raw kodo result (kept for kodo-
    specific stdout/stderr capture used in the kodo capture envelope).
    """
    raw = runner._run_subprocess(
        list(invocation.command),
        cwd=Path(invocation.working_directory),
        timeout=invocation.timeout_seconds or 0,
        env=dict(invocation.environment) if invocation.environment else None,
    )
    finished_at = _now()
    status = _status_from_raw(raw)

    rxp_result = RuntimeResult(
        invocation_id=invocation.invocation_id,
        runtime_name=invocation.runtime_name,
        runtime_kind=invocation.runtime_kind,
        status=status,
        exit_code=raw.exit_code,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
    )
    return rxp_result, raw


def _status_from_raw(raw: KodoRunResult) -> str:
    if "[timeout:" in (raw.stderr or ""):
        return "timed_out"
    return "succeeded" if raw.exit_code == 0 else "failed"


def _parse_iso(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


# ---------------------------------------------------------------------------
# Artifact extraction
# ---------------------------------------------------------------------------


def _extract_artifacts(stdout: str, stderr: str) -> list[KodoArtifactCapture]:
    """Extract any artifacts worth preserving from kodo's output streams."""
    artifacts: list[KodoArtifactCapture] = []

    combined = (stdout + "\n" + stderr).strip()
    if combined:
        excerpt = _truncate(combined, 4000)
        artifacts.append(
            KodoArtifactCapture(
                label="kodo run log",
                content=excerpt,
                artifact_type="log_excerpt",
            )
        )

    return artifacts


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)
