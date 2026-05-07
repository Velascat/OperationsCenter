# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/openclaw/invoke.py — OpenClaw invocation boundary.

OpenClawRunner is the low-level interface to the OpenClaw execution service.
Subclass it to provide a real implementation; use StubOpenClawRunner or a
MagicMock(spec=OpenClawRunner) in tests.

OpenClawBackendInvoker constructs an RxP ``RuntimeInvocation`` and hands
it to ``ExecutorRuntime`` (which routes ``runtime_kind="manual"`` to a
``ManualRunner`` registered with an openclaw-specific dispatcher),
receives an RxP ``RuntimeResult`` back, and assembles the
``OpenClawRunCapture`` for the normalizer.

Phase 2 + 3 of the OC runtime extraction applied to openclaw. Same
shape as archon's manual-kind path: openclaw is dispatched to an
external runner subclass, not run as a subprocess by OC.

Single-threaded by design: concurrent invokes would race on the
registered "manual" runner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from executor_runtime import ExecutorRuntime
from executor_runtime.runners import ManualRunner
from rxp.contracts import RuntimeInvocation, RuntimeResult

from .models import OpenClawArtifactCapture, OpenClawRunCapture, OpenClawPreparedRun


@dataclass
class OpenClawRunResult:
    """Raw result from a single OpenClaw invocation."""

    outcome: str
    exit_code: int = 0
    output_text: str = ""
    error_text: str = ""
    events: list[dict] = field(default_factory=list)
    reported_changed_files: list[dict] = field(default_factory=list)


class OpenClawRunner(ABC):
    """Low-level OpenClaw execution interface."""

    @abstractmethod
    def run(self, prepared: OpenClawPreparedRun) -> OpenClawRunResult:
        """Execute an OpenClaw run and return the raw result."""


class StubOpenClawRunner(OpenClawRunner):
    def __init__(self, result: OpenClawRunResult) -> None:
        self._result = result

    def run(self, prepared: OpenClawPreparedRun) -> OpenClawRunResult:  # noqa: ARG002 - stub ignores arg
        return self._result


class OpenClawBackendInvoker:
    """Invokes OpenClaw via ExecutorRuntime + ManualRunner."""

    def __init__(
        self,
        runner: OpenClawRunner,
        runtime: ExecutorRuntime | None = None,
    ) -> None:
        self._runner = runner
        self._runtime = runtime or ExecutorRuntime()

    def invoke(self, prepared: OpenClawPreparedRun) -> OpenClawRunCapture:
        started_at = _now()
        invocation = _build_invocation(prepared)

        raw_holder: list[OpenClawRunResult] = []

        def _dispatcher(inv: RuntimeInvocation) -> RuntimeResult:
            raw = self._runner.run(prepared)
            raw_holder.append(raw)
            timeout = _detect_timeout(raw)
            return _build_runtime_result(
                invocation=inv, raw=raw, timeout_hit=timeout,
                started_at=started_at, finished_at=_now(),
            )

        self._runtime.register("manual", ManualRunner(_dispatcher))
        rxp_result = self._runtime.run(invocation)

        if not raw_holder:
            return _capture_from_rejection(
                prepared=prepared, rxp_result=rxp_result,
                started_at=started_at, finished_at=_now(),
            )
        raw = raw_holder[0]

        finished_at = _parse_iso(rxp_result.finished_at)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        timeout_hit = rxp_result.status == "timed_out"

        artifacts = _extract_artifacts(raw.output_text or "", raw.error_text or "")
        changed_files_source = _determine_changed_files_source(raw)

        return OpenClawRunCapture(
            run_id=prepared.run_id,
            outcome=raw.outcome,
            exit_code=raw.exit_code,
            output_text=raw.output_text or "",
            error_text=raw.error_text or "",
            events=list(raw.events),
            artifacts=artifacts,
            reported_changed_files=list(raw.reported_changed_files),
            changed_files_source=changed_files_source,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            timeout_hit=timeout_hit,
        )


def _build_invocation(prepared: OpenClawPreparedRun) -> RuntimeInvocation:
    metadata: dict[str, str] = {
        "run_mode": getattr(prepared, "run_mode", "goal") or "goal",
        "task_branch": getattr(prepared, "task_branch", "") or "",
    }
    return RuntimeInvocation(
        invocation_id=prepared.run_id,
        runtime_name="openclaw",
        runtime_kind="manual",
        working_directory=str(prepared.repo_path),
        command=[
            "openclaw-run",
            "--run-mode", metadata["run_mode"],
            "--run-id", prepared.run_id,
        ],
        environment={},
        timeout_seconds=getattr(prepared, "timeout_seconds", 300) or None,
        metadata=metadata,
    )


def _build_runtime_result(
    *,
    invocation: RuntimeInvocation,
    raw: OpenClawRunResult,
    timeout_hit: bool,
    started_at: datetime,
    finished_at: datetime,
) -> RuntimeResult:
    if timeout_hit:
        status = "timed_out"
    elif raw.outcome == "success":
        status = "succeeded"
    elif raw.outcome == "partial":
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


def _detect_timeout(raw: OpenClawRunResult) -> bool:
    if raw.outcome == "timeout":
        return True
    err = (raw.error_text or "").lower()
    return any(s in err for s in ("[timeout:", "deadline exceeded", "execution timed out"))


def _parse_iso(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _capture_from_rejection(
    *,
    prepared: OpenClawPreparedRun,
    rxp_result: RuntimeResult,
    started_at: datetime,
    finished_at: datetime,
) -> OpenClawRunCapture:
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    return OpenClawRunCapture(
        run_id=prepared.run_id,
        outcome="failure",
        exit_code=-1,
        output_text="",
        error_text=rxp_result.error_summary or "executor runtime rejected invocation",
        events=[],
        artifacts=[],
        reported_changed_files=[],
        changed_files_source="unknown",
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        timeout_hit=False,
    )


def _determine_changed_files_source(raw: OpenClawRunResult) -> str:
    if raw.reported_changed_files:
        return "event_stream"
    return "unknown"


def _extract_artifacts(output_text: str, error_text: str) -> list[OpenClawArtifactCapture]:
    artifacts: list[OpenClawArtifactCapture] = []
    combined = (output_text + "\n" + error_text).strip()
    if combined:
        excerpt = _truncate(combined, 4000)
        artifacts.append(
            OpenClawArtifactCapture(
                label="openclaw run log",
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
