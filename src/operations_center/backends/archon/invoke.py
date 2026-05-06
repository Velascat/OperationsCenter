# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/archon/invoke.py — Archon invocation boundary.

ArchonAdapter is the low-level interface to the Archon workflow service.
Subclass it to provide a real implementation; use the provided stub or a
MagicMock(spec=ArchonAdapter) in tests.

ArchonBackendInvoker wraps ArchonAdapter, constructs an RxP
``RuntimeInvocation`` record describing the workflow that's about to
run, dispatches to the adapter, and converts the raw result back into
an ``ArchonRunCapture`` for the normalizer plus an RxP
``RuntimeResult`` for telemetry.

Phase 2 of the OC runtime extraction: archon's adapter call is not
yet driven by the ExecutorRuntime — archon is an out-of-process
service, not a subprocess. The RxP types here document what's
running so a future HTTP/manual runner extraction can land without a
contract change.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from rxp.contracts import RuntimeInvocation, RuntimeResult

from .models import ArchonArtifactCapture, ArchonRunCapture, ArchonWorkflowConfig


# ---------------------------------------------------------------------------
# ArchonAdapter — low-level invocation protocol
# ---------------------------------------------------------------------------


@dataclass
class ArchonRunResult:
    """Raw result from a single Archon workflow invocation."""

    outcome: str          # "success", "failure", "timeout", "partial"
    exit_code: int = 0
    output_text: str = ""
    error_text: str = ""
    workflow_events: list[dict] = field(default_factory=list)


class ArchonAdapter(ABC):
    """Low-level Archon workflow interface.

    Override run() in a concrete subclass to connect to a real Archon service.
    In tests, replace with MagicMock(spec=ArchonAdapter) or StubArchonAdapter.

    The implementation detail of how Archon is invoked — subprocess, Python
    API, RPC, etc. — belongs here and must not escape into the wider adapter.
    """

    @abstractmethod
    def run(self, config: ArchonWorkflowConfig) -> ArchonRunResult:
        """Execute an Archon workflow and return the raw result."""


class StubArchonAdapter(ArchonAdapter):
    """Minimal stub for tests that need a deterministic Archon response.

    Inject a pre-built ArchonRunResult at construction time.
    """

    def __init__(self, result: ArchonRunResult) -> None:
        self._result = result

    def run(self, _config: ArchonWorkflowConfig) -> ArchonRunResult:
        return self._result


# ---------------------------------------------------------------------------
# ArchonBackendInvoker
# ---------------------------------------------------------------------------


class ArchonBackendInvoker:
    """Invokes Archon for a prepared workflow run and returns ArchonRunCapture.

    Phase 2 (RxP wire):
      1. Build an RxP ``RuntimeInvocation`` describing the workflow.
      2. Dispatch to the abstract ``ArchonAdapter``.
      3. Synthesize an RxP ``RuntimeResult`` from the raw archon
         output (telemetry; not yet plumbed into observability).
      4. Assemble ``ArchonRunCapture`` for the normalizer.
    """

    def __init__(
        self,
        archon_adapter: ArchonAdapter,
    ) -> None:
        self._archon = archon_adapter

    def invoke(self, config: ArchonWorkflowConfig) -> ArchonRunCapture:
        """Execute the Archon workflow and return structured capture."""
        started_at = _now()
        env = self._build_env(config.env_overrides)

        effective_config = config
        if env != os.environ:
            from dataclasses import replace
            effective_config = replace(config, env_overrides=env)

        invocation = _build_invocation(effective_config)

        raw = self._archon.run(effective_config)

        finished_at = _now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        timeout_hit = (
            raw.outcome == "timeout"
            or any(s in (raw.error_text or "").lower() for s in ("[timeout:", "deadline exceeded"))
        )

        # RxP RuntimeResult — telemetry only for now; not on the
        # canonical surface. Future observability layers will consume this.
        _rxp_result = _build_runtime_result(
            invocation=invocation, raw=raw, timeout_hit=timeout_hit,
            started_at=started_at, finished_at=finished_at,
        )

        artifacts = _extract_artifacts(raw.output_text or "", raw.error_text or "")

        return ArchonRunCapture(
            run_id=config.run_id,
            outcome=raw.outcome,
            exit_code=raw.exit_code,
            output_text=raw.output_text or "",
            error_text=raw.error_text or "",
            workflow_events=list(raw.workflow_events),
            artifacts=artifacts,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            timeout_hit=timeout_hit,
        )

    # ------------------------------------------------------------------

    def _build_env(self, overrides: dict[str, str]) -> dict[str, str]:
        env = os.environ.copy()
        env.update(overrides)
        return env


# ---------------------------------------------------------------------------
# RxP wire helpers
# ---------------------------------------------------------------------------


def _build_invocation(config: ArchonWorkflowConfig) -> RuntimeInvocation:
    """Construct an RxP RuntimeInvocation describing the archon workflow.

    runtime_kind is "manual" because archon is an out-of-process
    service that OC dispatches to via the abstract ArchonAdapter
    rather than a subprocess. The "command" is descriptive — it
    captures the workflow type and run id so observers know what the
    invocation requested without coupling to archon-specific fields.
    """
    metadata: dict[str, str] = {
        "workflow_type": config.workflow_type,
        "task_branch": config.task_branch or "",
    }
    for k, v in config.metadata.items():
        if isinstance(v, str):
            metadata.setdefault(k, v)

    return RuntimeInvocation(
        invocation_id=config.run_id,
        runtime_name="archon",
        runtime_kind="manual",
        working_directory=str(config.repo_path),
        command=[
            "archon-workflow",
            "--workflow-type", config.workflow_type,
            "--run-id", config.run_id,
        ],
        environment=dict(config.env_overrides),
        timeout_seconds=config.timeout_seconds if config.timeout_seconds > 0 else None,
        metadata=metadata,
    )


def _build_runtime_result(
    *,
    invocation: RuntimeInvocation,
    raw: ArchonRunResult,
    timeout_hit: bool,
    started_at: datetime,
    finished_at: datetime,
) -> RuntimeResult:
    """Synthesize an RxP RuntimeResult from the archon-specific raw result."""
    if timeout_hit:
        status = "timed_out"
    elif raw.outcome == "success":
        status = "succeeded"
    elif raw.outcome == "partial":
        # No exact RxP analogue. Map partial → succeeded; downstream
        # normalization decides whether the partial counts as failure.
        status = "succeeded"
    else:
        status = "failed"

    error_summary = raw.error_text.strip()[:200] if raw.error_text else None

    return RuntimeResult(
        invocation_id=invocation.invocation_id,
        runtime_name=invocation.runtime_name,
        runtime_kind=invocation.runtime_kind,
        status=status,
        exit_code=raw.exit_code,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        error_summary=error_summary,
    )


# ---------------------------------------------------------------------------
# Artifact extraction
# ---------------------------------------------------------------------------


def _extract_artifacts(output_text: str, error_text: str) -> list[ArchonArtifactCapture]:
    artifacts: list[ArchonArtifactCapture] = []

    combined = (output_text + "\n" + error_text).strip()
    if combined:
        excerpt = _truncate(combined, 4000)
        artifacts.append(
            ArchonArtifactCapture(
                label="archon workflow log",
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
