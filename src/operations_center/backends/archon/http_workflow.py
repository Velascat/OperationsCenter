# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ArchonHttpWorkflowDispatcher — Phase-A invoker for the real Archon API.

Implements the design in
``WorkStation/docs/architecture/adapters/archon-real-workflow-integration.md``.

Flow:
    health probe
        ↓
    POST /api/conversations  (D2: per-task conversation)
        ↓
    resolve workflow_name from config.workflow_type
        ↓
    AsyncHttpRunner kickoff + poll-until-terminal
      - kickoff: POST /api/workflows/{name}/run → 200 {accepted, status:"started"}
      - AsyncHttpRunner sees 200 + non-terminal status → falls through to poll
      - poll: GET /api/workflows/runs/by-worker/{conv_id}
      - 404 from by-worker (run not yet registered) is tolerated via
        http.poll_pending_codes
        ↓
    once a terminal status is seen, fetch GET /api/workflows/runs/{run_id}
    for the full event list (by-worker response has no events)
        ↓
    map run.status → ArchonRunCapture.outcome (D6)
        ↓
    on terminal:
      - paused  → leave run; surface as outcome="partial"
      - other   → POST /abandon (best-effort)
    on timeout:
      - POST /cancel; outcome="timeout"

This dispatcher uses ``runtime_kind="http_async"`` so the production
path is plain ExecutorRuntime + AsyncHttpRunner (no ManualRunner closure).
``goal_text`` reaches Archon verbatim per D1; ``task_branch`` lives in
``RuntimeInvocation.metadata['archon.task_branch']`` for OC-side
observability only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from executor_runtime import ExecutorRuntime
from executor_runtime.runners import AsyncHttpRunner
from rxp.contracts import RuntimeInvocation

from .http_client import (
    DEFAULT_BASE_URL,
    archon_abandon_run,
    archon_cancel_run,
    archon_create_conversation,
    archon_get_run_by_worker,
    archon_get_run_detail,
    archon_health_probe,
)
from .models import ArchonArtifactCapture, ArchonRunCapture, ArchonWorkflowConfig

logger = logging.getLogger(__name__)

# Default workflow_type → Archon workflow name. Mapped to workflows that
# actually ship as bundled defaults in Velascat/Archon main (verified
# against a live container 2026-05-07 — see project_archon_api_quirks
# memory + WS design doc). Operators override via
# ArchonSettings.workflow_names when they ship custom YAML workflows.
DEFAULT_WORKFLOW_NAMES: dict[str, str] = {
    "goal":    "archon-assist",            # general-purpose; always available
    "fix_pr":  "archon-fix-github-issue",  # NB: not "...-dag" (that's a different sibling)
    "test":    "archon-test-loop-dag",
    "improve": "archon-refactor-safely",
}

_TERMINAL_STATES = ("completed", "failed", "cancelled", "paused")
_SUCCESS_STATES = ("completed",)
# Archon's by-worker endpoint 404s until the run is registered to a worker.
# AsyncHttpRunner treats codes in this list as "still pending, keep polling".
_PENDING_POLL_CODES = ("404",)


class ArchonHttpWorkflowDispatcher:
    """Production-path archon dispatcher built on AsyncHttpRunner.

    Construction params:
      - ``base_url``: archon root URL (default DEFAULT_BASE_URL).
      - ``runtime``: shared ExecutorRuntime; AsyncHttpRunner is registered
        for ``http_async`` if not already.
      - ``workflow_names``: workflow_type → archon name override map.
      - ``poll_interval_seconds``: between status polls (default 2.0).
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        runtime: ExecutorRuntime | None = None,
        workflow_names: dict[str, str] | None = None,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._runtime = runtime or ExecutorRuntime()
        if not self._runtime.is_registered("http_async"):
            self._runtime.register("http_async", AsyncHttpRunner())
        self._workflow_names = (
            dict(workflow_names) if workflow_names else dict(DEFAULT_WORKFLOW_NAMES)
        )
        self._poll_interval = poll_interval_seconds

    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    def dispatch(self, config: ArchonWorkflowConfig) -> ArchonRunCapture:
        """Run a workflow end-to-end. Returns a structured capture."""
        started_at = _now()

        # ── Step 1: Pre-flight health probe ──────────────────────────
        probe = archon_health_probe(self._base_url)
        if not probe.ok:
            return _failure_capture(
                config=config,
                started_at=started_at,
                error_text=f"archon unreachable at {self._base_url}: {probe.summary}",
            )

        # ── Step 2: Resolve workflow name ────────────────────────────
        workflow_name = self._workflow_names.get(config.workflow_type)
        if not workflow_name:
            return _failure_capture(
                config=config,
                started_at=started_at,
                error_text=f"unknown archon workflow_type: {config.workflow_type!r}",
            )

        # ── Step 3: Create conversation ──────────────────────────────
        conv = archon_create_conversation(
            self._base_url,
            codebase_id=None,  # D2/Q1: omit in v1; archon infers
        )
        if not conv.ok:
            return _failure_capture(
                config=config,
                started_at=started_at,
                error_text=f"conversation create failed: {conv.error_summary}",
            )

        logger.info(
            "archon dispatch: workflow=%s conversation=%s repo=%s",
            workflow_name, conv.conversation_id, config.repo_path,
        )

        # ── Step 4: Build http_async RuntimeInvocation ───────────────
        invocation = self._build_invocation(
            config=config,
            workflow_name=workflow_name,
            conversation_id=conv.conversation_id,
        )

        # ── Step 5: Dispatch via AsyncHttpRunner ─────────────────────
        rxp_result = self._runtime.run(invocation)

        # ── Step 6: Resolve run_id via by-worker, then full detail ────
        finished_at = _now()
        timeout_hit = rxp_result.status == "timed_out" or _is_timeout_summary(
            rxp_result.error_summary,
        )

        # By-worker gives us the run_id; we need /runs/{run_id} for events.
        by_worker = archon_get_run_by_worker(self._base_url, conv.conversation_id)
        run_id = by_worker.run_id if by_worker.ok else ""
        run_detail = (
            archon_get_run_detail(self._base_url, run_id) if run_id
            else by_worker  # carry the error_summary forward
        )

        if timeout_hit:
            if run_id:
                archon_cancel_run(self._base_url, run_id)
            return _timeout_capture(
                config=config,
                run_id=run_id,
                workflow_events=run_detail.events,
                started_at=started_at,
                finished_at=finished_at,
            )

        if not run_detail.ok:
            return _failure_capture(
                config=config,
                started_at=started_at,
                finished_at=finished_at,
                error_text=(
                    f"workflow finished but run lookup failed: "
                    f"{run_detail.error_summary}; "
                    f"runner status={rxp_result.status}"
                ),
            )

        outcome, exit_code, error_text = _map_status(
            run_detail.status, run_detail.metadata,
        )
        output_text = _extract_output_text(run_detail.events)
        artifacts = _extract_artifacts(run_detail.events)

        # D2: abandon non-paused terminal runs. Paused runs stay alive so
        # operators can /approve via the Archon CLI/UI.
        if outcome != "partial":
            archon_abandon_run(self._base_url, run_detail.run_id)

        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        return ArchonRunCapture(
            run_id=config.run_id,
            outcome=outcome,
            exit_code=exit_code,
            output_text=output_text,
            error_text=error_text,
            workflow_events=list(run_detail.events),
            artifacts=artifacts,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            timeout_hit=False,
        )

    # ------------------------------------------------------------------

    def _build_invocation(
        self,
        *,
        config: ArchonWorkflowConfig,
        workflow_name: str,
        conversation_id: str,
    ) -> RuntimeInvocation:
        """Build the RxP RuntimeInvocation for AsyncHttpRunner."""
        kickoff_url = f"{self._base_url}/api/workflows/{workflow_name}/run"
        # No {run_id} template — Archon's by-worker URL is keyed on
        # conversation_id which we have outside the kickoff response.
        poll_url = f"{self._base_url}/api/workflows/runs/by-worker/{conversation_id}"

        # D1 strict: goal_text reaches Archon verbatim, no framing.
        # Per-request runtime override (provider/model) is sent in the body
        # only when populated — Archon's patched route (PATCH-001) accepts
        # these fields optionally, and stock/upstream Archon ignores them
        # gracefully (Zod schema is .extend()-able, not .strict()).
        body_payload: dict[str, Any] = {
            "conversationId": conversation_id,
            "message": config.goal_text,
        }
        if config.provider:
            body_payload["provider"] = config.provider
        if config.model:
            body_payload["model"] = config.model
        body = json.dumps(body_payload)

        metadata: dict[str, str] = {
            "http.url": kickoff_url,
            "http.method": "POST",
            "http.body": body,
            "http.body_format": "json",
            # AsyncHttpRunner: 200 acks (status="started") fall through to poll
            # because "started" is not in poll_terminal_states.
            "http.poll_url_template": poll_url,
            "http.poll_status_path": "run.status",
            "http.poll_terminal_states": ",".join(_TERMINAL_STATES),
            "http.poll_success_states": ",".join(_SUCCESS_STATES),
            "http.poll_pending_codes": ",".join(_PENDING_POLL_CODES),
            "http.poll_interval_seconds": str(self._poll_interval),
            # OC-side observability — never reaches Archon. D1 strict.
            "archon.conversation_id": conversation_id,
            "archon.workflow_name": workflow_name,
            "archon.task_branch": config.task_branch or "",
            "archon.workflow_type": config.workflow_type,
            # OC-side observability for the per-request runtime override —
            # never reaches Archon (those go in the kickoff body above).
            "archon.runtime_provider": config.provider or "",
            "archon.runtime_model": config.model or "",
        }

        # Carry caller-supplied metadata through (string-only).
        for k, v in (config.metadata or {}).items():
            if isinstance(v, str):
                metadata.setdefault(f"archon.config.{k}", v)

        return RuntimeInvocation(
            invocation_id=config.run_id,
            runtime_name="archon",
            runtime_kind="http_async",
            working_directory=str(config.repo_path),
            command=[
                "archon-workflow", "--workflow", workflow_name,
                "--run-id", config.run_id,
            ],
            environment={},
            timeout_seconds=config.timeout_seconds,
            input_payload_path=None,
            output_result_path=None,
            artifact_directory=None,
            metadata=metadata,
        )


# ──────────────────────────────────────────────────────────────────────────
# Status mapping (D6) + output/artifact extraction
# ──────────────────────────────────────────────────────────────────────────


def _map_status(status: str, metadata: dict[str, Any]) -> tuple[str, int, str]:
    """Archon status → (outcome, exit_code, error_text). D6 in design doc."""
    status = status.lower()
    if status == "completed":
        return "success", 0, ""
    if status == "failed":
        msg = str(
            metadata.get("failure_reason")
            or metadata.get("error")
            or "workflow failed"
        )
        return "failure", 1, msg
    if status == "cancelled":
        return "failure", 1, "workflow cancelled"
    if status == "paused":
        approval = metadata.get("approval") or {}
        node_id = approval.get("nodeId") if isinstance(approval, dict) else None
        msg = (
            f"archon paused for approval at node {node_id}"
            if node_id else "archon paused for approval"
        )
        return "partial", 2, msg
    return "failure", 1, f"unexpected archon status: {status!r}"


def _extract_output_text(events: list[dict[str, Any]]) -> str:
    """Synthesize output_text from the last `node_completed` event (Q3 v1)."""
    for event in reversed(events):
        if event.get("event_type") == "node_completed":
            data = event.get("data") or {}
            output = data.get("node_output")
            if isinstance(output, str) and output.strip():
                return output
    return ""


def _extract_artifacts(events: list[dict[str, Any]]) -> list[ArchonArtifactCapture]:
    """Pick up any artifact-bearing events. v1: scan for ``data.artifacts``."""
    out: list[ArchonArtifactCapture] = []
    for event in events:
        data = event.get("data") or {}
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, list):
            continue
        for art in artifacts:
            if not isinstance(art, dict):
                continue
            label = str(art.get("label") or art.get("name") or "")
            content = str(art.get("content") or art.get("body") or "")
            artifact_type = str(art.get("type") or art.get("artifact_type") or "log")
            if not (label or content):
                continue
            out.append(ArchonArtifactCapture(
                label=label, content=content, artifact_type=artifact_type,
            ))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Capture builders for failure paths
# ──────────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_timeout_summary(summary: str | None) -> bool:
    if not summary:
        return False
    s = summary.lower()
    return "timeout" in s or "deadline" in s


def _failure_capture(
    *,
    config: ArchonWorkflowConfig,
    started_at: datetime,
    finished_at: datetime | None = None,
    error_text: str,
) -> ArchonRunCapture:
    finished_at = finished_at or _now()
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    return ArchonRunCapture(
        run_id=config.run_id,
        outcome="failure",
        exit_code=-1,
        output_text="",
        error_text=error_text,
        workflow_events=[],
        artifacts=[],
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        timeout_hit=False,
    )


def _timeout_capture(
    *,
    config: ArchonWorkflowConfig,
    run_id: str,
    workflow_events: list[dict[str, Any]],
    started_at: datetime,
    finished_at: datetime,
) -> ArchonRunCapture:
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    return ArchonRunCapture(
        run_id=config.run_id,
        outcome="timeout",
        exit_code=-1,
        output_text="",
        error_text=f"archon workflow exceeded timeout of {config.timeout_seconds}s",
        workflow_events=list(workflow_events),
        artifacts=[],
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        timeout_hit=True,
    )
