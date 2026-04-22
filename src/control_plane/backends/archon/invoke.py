"""
backends/archon/invoke.py — Archon invocation boundary.

ArchonAdapter is the low-level interface to the Archon workflow service.
Subclass it to provide a real implementation; use the provided stub or a
MagicMock(spec=ArchonAdapter) in tests.

ArchonBackendInvoker wraps ArchonAdapter and returns a structured
ArchonRunCapture, isolating invocation mechanics from the rest of the adapter.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone

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


class ArchonAdapter:
    """Low-level Archon workflow interface.

    Override run() in a concrete subclass to connect to a real Archon service.
    In tests, replace with MagicMock(spec=ArchonAdapter) or StubArchonAdapter.

    The implementation detail of how Archon is invoked — subprocess, Python
    API, RPC, etc. — belongs here and must not escape into the wider adapter.
    """

    def run(self, config: ArchonWorkflowConfig) -> ArchonRunResult:
        """Execute an Archon workflow and return the raw result.

        Raises:
            NotImplementedError: in the base class.
        """
        raise NotImplementedError(
            "ArchonAdapter.run() must be implemented by a concrete subclass."
        )


class StubArchonAdapter(ArchonAdapter):
    """Minimal stub for tests that need a deterministic Archon response.

    Inject a pre-built ArchonRunResult at construction time.
    """

    def __init__(self, result: ArchonRunResult) -> None:
        self._result = result

    def run(self, config: ArchonWorkflowConfig) -> ArchonRunResult:
        return self._result


# ---------------------------------------------------------------------------
# ArchonBackendInvoker
# ---------------------------------------------------------------------------


class ArchonBackendInvoker:
    """Invokes Archon for a prepared workflow run and returns ArchonRunCapture.

    The invoker:
    - delegates execution to the underlying ArchonAdapter
    - measures wall-clock timing
    - detects timeout signals in the output
    - extracts log artifacts from combined output
    - wraps the raw result into ArchonRunCapture
    """

    def __init__(
        self,
        archon_adapter: ArchonAdapter,
        switchboard_url: str = "",
    ) -> None:
        self._archon = archon_adapter
        # Legacy compatibility-only transport override. The canonical runtime
        # no longer depends on SwitchBoard as an execution proxy.
        self._switchboard_url = switchboard_url.rstrip("/")
        if self._switchboard_url:
            warnings.warn(
                "switchboard_url is a legacy compatibility-only execution proxy override. "
                "The canonical execution path no longer routes backend traffic through SwitchBoard.",
                DeprecationWarning,
                stacklevel=2,
            )

    def invoke(self, config: ArchonWorkflowConfig) -> ArchonRunCapture:
        """Execute the Archon workflow and return structured capture."""
        started_at = _now()
        env = self._build_env(config.env_overrides)

        # Inject any env overrides derived from invoker config
        effective_config = config
        if env != os.environ:
            from dataclasses import replace
            effective_config = replace(config, env_overrides=env)

        raw = self._archon.run(effective_config)

        finished_at = _now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        timeout_hit = (
            raw.outcome == "timeout"
            or any(s in (raw.error_text or "").lower() for s in ("[timeout:", "deadline exceeded"))
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
        if self._switchboard_url:
            env["OPENAI_API_BASE"] = f"{self._switchboard_url}/v1"
        env.update(overrides)
        return env


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
