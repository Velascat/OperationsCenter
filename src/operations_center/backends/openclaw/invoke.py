# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/openclaw/invoke.py — OpenClaw invocation boundary.

OpenClawRunner is the low-level interface to the OpenClaw execution service.
Subclass it to provide a real implementation; use StubOpenClawRunner or a
MagicMock(spec=OpenClawRunner) in tests.

OpenClawBackendInvoker wraps OpenClawRunner and returns a structured
OpenClawRunCapture, isolating invocation mechanics from the rest of the adapter.

The detail of how OpenClaw is invoked — subprocess, Python entrypoint, internal
API, or another mechanism — belongs here and must not escape into the wider adapter.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import OpenClawArtifactCapture, OpenClawRunCapture, OpenClawPreparedRun


# ---------------------------------------------------------------------------
# OpenClawRunner — low-level invocation protocol
# ---------------------------------------------------------------------------


@dataclass
class OpenClawRunResult:
    """Raw result from a single OpenClaw invocation."""

    outcome: str          # "success", "failure", "timeout", "partial"
    exit_code: int = 0
    output_text: str = ""
    error_text: str = ""
    events: list[dict] = field(default_factory=list)
    reported_changed_files: list[dict] = field(default_factory=list)


class OpenClawRunner:
    """Low-level OpenClaw execution interface.

    Override run() in a concrete subclass to connect to a real OpenClaw service.
    In tests, replace with MagicMock(spec=OpenClawRunner) or StubOpenClawRunner.

    The implementation detail of how OpenClaw is invoked — subprocess, Python
    API, RPC, etc. — belongs here and must not escape into the wider adapter.

    reported_changed_files is populated when OpenClaw itself surfaces a list of
    files it modified. When absent, the invoker falls back to git diff.
    """

    @abstractmethod
    def run(self, prepared: OpenClawPreparedRun) -> OpenClawRunResult:
        """Execute an OpenClaw run and return the raw result."""


class StubOpenClawRunner(OpenClawRunner):
    """Minimal stub for tests that need a deterministic OpenClaw response.

    Inject a pre-built OpenClawRunResult at construction time.
    """

    def __init__(self, result: OpenClawRunResult) -> None:
        self._result = result

    def run(self, prepared: OpenClawPreparedRun) -> OpenClawRunResult:
        return self._result


# ---------------------------------------------------------------------------
# OpenClawBackendInvoker
# ---------------------------------------------------------------------------


class OpenClawBackendInvoker:
    """Invokes OpenClaw for a prepared run and returns OpenClawRunCapture.

    The invoker:
    - delegates execution to the underlying OpenClawRunner
    - measures wall-clock timing
    - detects timeout signals in the output
    - extracts log artifacts from combined output
    - extracts changed files from events when OpenClaw reports them directly
    - wraps the raw result into OpenClawRunCapture with an explicit
      changed_files_source to support honest normalization downstream
    """

    def __init__(
        self,
        runner: OpenClawRunner,
    ) -> None:
        self._runner = runner

    def invoke(self, prepared: OpenClawPreparedRun) -> OpenClawRunCapture:
        """Execute the OpenClaw run and return structured capture."""
        started_at = _now()

        raw = self._runner.run(prepared)

        finished_at = _now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        timeout_hit = (
            raw.outcome == "timeout"
            or any(
                s in (raw.error_text or "").lower()
                for s in ("[timeout:", "deadline exceeded", "execution timed out")
            )
        )

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _determine_changed_files_source(raw: OpenClawRunResult) -> str:
    """Return the most appropriate changed_files_source label.

    If OpenClaw reported files in its event stream, those are inferred (the
    normalizer will use them if git diff is unavailable). The authoritative
    "git_diff" label is set by the normalizer after running git, not here.
    """
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
