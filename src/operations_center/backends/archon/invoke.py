# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/archon/invoke.py — Archon invocation boundary.

ArchonAdapter is the low-level interface to the Archon workflow service.
Subclass it to provide a real implementation; use the provided stub or a
MagicMock(spec=ArchonAdapter) in tests.

ArchonBackendInvoker constructs an RxP ``RuntimeInvocation``, hands it
to ``ExecutorRuntime`` (which routes ``runtime_kind="manual"`` to a
``ManualRunner`` registered with an archon-specific dispatcher),
receives an RxP ``RuntimeResult`` back, and assembles the
``ArchonRunCapture`` for the normalizer.

Phase 3 (this PR): archon goes through ExecutorRuntime. Because
archon's abstract adapter needs the full ``ArchonWorkflowConfig``
(goal_text / constraints / validation_commands / etc. don't fit in
``RuntimeInvocation.metadata: dict[str, str]``), the dispatcher is a
per-call closure that captures ``effective_config`` and returns the
``RuntimeResult`` synthesized from the raw archon response. The
invoker is single-threaded by design — concurrent calls would race
on the registered runner.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from executor_runtime import ExecutorRuntime
from executor_runtime.runners import ManualRunner
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

    def run(self, config: ArchonWorkflowConfig) -> ArchonRunResult:  # noqa: ARG002 - stub ignores arg
        return self._result


class HttpArchonAdapter(ArchonAdapter):
    """Concrete archon adapter backed by Archon's HTTP workflow API.

    Connects to a running Archon instance (deployed by WorkStation
    via ``compose/profiles/archon.yml``) and dispatches workflows
    end-to-end: conversation create → workflow run → poll-until-terminal
    → status mapping → abandon/cancel. The actual transport flow lives
    in :class:`ArchonHttpWorkflowDispatcher` (``http_workflow.py``);
    this adapter is a thin shim that satisfies the ``ArchonAdapter``
    ABC and converts the dispatcher's ``ArchonRunCapture`` to the
    ``ArchonRunResult`` the invoker expects.

    See ``WorkStation/docs/architecture/adapters/archon-real-workflow-integration.md``
    for the design.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        workflow_names: dict[str, str] | None = None,
        poll_interval_seconds: float = 2.0,
        runtime: ExecutorRuntime | None = None,
    ) -> None:
        # Lazy import keeps the abstract module loadable without httpx.
        from operations_center.backends.archon.http_client import DEFAULT_BASE_URL
        from operations_center.backends.archon.http_workflow import (
            ArchonHttpWorkflowDispatcher,
        )
        self._base_url = base_url or DEFAULT_BASE_URL
        self._dispatcher = ArchonHttpWorkflowDispatcher(
            base_url=self._base_url,
            runtime=runtime,
            workflow_names=workflow_names,
            poll_interval_seconds=poll_interval_seconds,
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def run(self, config: ArchonWorkflowConfig) -> ArchonRunResult:
        capture = self._dispatcher.dispatch(config)
        return ArchonRunResult(
            outcome=capture.outcome,
            exit_code=capture.exit_code,
            output_text=capture.output_text,
            error_text=capture.error_text,
            workflow_events=list(capture.workflow_events),
        )


# ---------------------------------------------------------------------------
# ArchonBackendInvoker
# ---------------------------------------------------------------------------


class ArchonBackendInvoker:
    """Invokes Archon for a prepared workflow run and returns ArchonRunCapture.

    Phase 3 (ExecutorRuntime delegation, manual kind):
      1. Build an RxP ``RuntimeInvocation`` (runtime_kind="manual").
      2. Register a per-call dispatcher closure with ExecutorRuntime
         that captures the effective config; the dispatcher calls
         the abstract ``ArchonAdapter`` and synthesizes the RxP
         ``RuntimeResult``.
      3. ``ExecutorRuntime.run(invocation)`` routes through the
         registered ``ManualRunner`` to the dispatcher.
      4. The captured raw result populates ``ArchonRunCapture`` for
         the normalizer.

    Single-threaded by design: concurrent calls would race on the
    registered "manual" runner. Archon dispatches are serial today.
    """

    def __init__(
        self,
        archon_adapter: ArchonAdapter,
        runtime: ExecutorRuntime | None = None,
    ) -> None:
        self._archon = archon_adapter
        self._runtime = runtime or ExecutorRuntime()

    def invoke(self, config: ArchonWorkflowConfig) -> ArchonRunCapture:
        """Execute the Archon workflow and return structured capture."""
        started_at = _now()
        env = self._build_env(config.env_overrides)

        effective_config = config
        if env != os.environ:
            from dataclasses import replace
            effective_config = replace(config, env_overrides=env)

        invocation = _build_invocation(effective_config)

        # Per-call dispatcher captures the full ArchonWorkflowConfig
        # (the abstract adapter needs goal_text / validation_commands
        # / etc. that don't fit in RuntimeInvocation.metadata's
        # dict[str, str] shape).
        raw_holder: list[ArchonRunResult] = []

        def _dispatcher(inv: RuntimeInvocation) -> RuntimeResult:
            raw = self._archon.run(effective_config)
            raw_holder.append(raw)
            timeout = _detect_timeout(raw)
            return _build_runtime_result(
                invocation=inv, raw=raw, timeout_hit=timeout,
                started_at=started_at, finished_at=_now(),
            )

        self._runtime.register("manual", ManualRunner(_dispatcher))
        rxp_result = self._runtime.run(invocation)

        from operations_center.backends._runtime_ref import runtime_invocation_ref
        ref = runtime_invocation_ref(invocation, rxp_result)

        if not raw_holder:
            # ExecutorRuntime rejected the invocation before our
            # dispatcher ran (e.g. no runner registered for the kind).
            cap = _capture_from_rejection(
                config=config, rxp_result=rxp_result,
                started_at=started_at, finished_at=_now(),
            )
            cap.invocation_ref = ref
            return cap
        raw = raw_holder[0]

        finished_at = _parse_iso(rxp_result.finished_at)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        timeout_hit = rxp_result.status == "timed_out"

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
            invocation_ref=ref,
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


def _detect_timeout(raw: "ArchonRunResult") -> bool:
    if raw.outcome == "timeout":
        return True
    err = (raw.error_text or "").lower()
    return any(s in err for s in ("[timeout:", "deadline exceeded"))


def _parse_iso(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _capture_from_rejection(
    *,
    config: ArchonWorkflowConfig,
    rxp_result: RuntimeResult,
    started_at: datetime,
    finished_at: datetime,
) -> ArchonRunCapture:
    """ExecutorRuntime rejected the invocation before dispatcher ran.

    This shouldn't happen in normal flow (we register the manual
    runner before calling run), but the rejection path keeps the
    invariant that ``invoke`` always returns an ArchonRunCapture.
    """
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    return ArchonRunCapture(
        run_id=config.run_id,
        outcome="failure",
        exit_code=-1,
        output_text="",
        error_text=rxp_result.error_summary or "executor runtime rejected invocation",
        workflow_events=[],
        artifacts=[],
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        timeout_hit=False,
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
