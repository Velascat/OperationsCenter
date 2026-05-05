# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/kodo/invoke.py — kodo invocation boundary.

KodoBackendInvoker isolates how kodo is launched. The rest of the system
does not need to know whether kodo runs as a subprocess, through a shim,
or via any other mechanism — that detail belongs here.

The invoker delegates to the existing KodoAdapter (subprocess layer) and
wraps the result into KodoRunCapture for normalization.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from operations_center.adapters.kodo.adapter import KodoAdapter

from .models import KodoArtifactCapture, KodoPreparedRun, KodoRunCapture


class KodoBackendInvoker:
    """Invokes kodo for a prepared run and returns a structured KodoRunCapture.

    The invoker:
    - writes the goal file into the workspace before running
    - delegates subprocess management to KodoAdapter
    - measures wall-clock timing
    - wraps the raw result into KodoRunCapture
    - cleans up the goal file on exit

    Args:
        kodo_adapter: The underlying KodoAdapter (subprocess layer).
    """

    def __init__(
        self,
        kodo_adapter: KodoAdapter,
    ) -> None:
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

        env = self._build_env(prepared.env_overrides)
        # Build a per-call profile override when binder pinned an orchestrator.
        profile = None
        if prepared.orchestrator_override is not None:
            base = self._kodo.settings
            profile = base.model_copy(update={"orchestrator": prepared.orchestrator_override})
        try:
            raw = self._kodo.run(
                prepared.goal_file_path,
                prepared.repo_path,
                env=env,
                kodo_mode=prepared.kodo_mode,
                profile=profile,
            )
        finally:
            try:
                prepared.goal_file_path.unlink(missing_ok=True)
            except Exception:
                pass

        finished_at = _now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        timeout_hit = "[timeout:" in (raw.stderr or "")
        rate_limited = KodoAdapter.is_orchestrator_rate_limited(raw)
        quota_exhausted = KodoAdapter.is_quota_exhausted(raw)

        artifacts = _extract_artifacts(raw.stdout or "", raw.stderr or "")

        return KodoRunCapture(
            run_id=prepared.run_id,
            exit_code=raw.exit_code,
            stdout=raw.stdout or "",
            stderr=raw.stderr or "",
            command=list(raw.command),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            timeout_hit=timeout_hit,
            rate_limited=rate_limited,
            quota_exhausted=quota_exhausted,
            artifacts=artifacts,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_env(self, overrides: dict[str, str]) -> dict[str, str]:
        env = os.environ.copy()
        env.update(overrides)
        return env


# ---------------------------------------------------------------------------
# Artifact extraction
# ---------------------------------------------------------------------------

def _extract_artifacts(stdout: str, stderr: str) -> list[KodoArtifactCapture]:
    """Extract any artifacts worth preserving from kodo's output streams."""
    artifacts: list[KodoArtifactCapture] = []

    # Capture combined log output as a log_excerpt artifact
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
