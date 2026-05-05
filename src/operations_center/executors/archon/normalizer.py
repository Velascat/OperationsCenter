# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Archon normalizer — Phase 2.

Maps a raw Archon workflow capture into the normalized CxRP ExecutionResult
shape. Archon emits multi-step workflow events; this normalizer flattens
them into one ExecutionResult per the spec's "no internal framework
shapes leak past the adapter" rule. The flattened internal trace lives
under ``evidence.extensions.internal_trace_summary``.
"""
from __future__ import annotations

from typing import Any

from cxrp.contracts import Evidence, ExecutionResult
from cxrp.vocabulary.status import ExecutionStatus


class NormalizationError(ValueError):
    """Raised when raw Archon output cannot be mapped to ExecutionResult."""


_OUTCOME_TO_STATUS = {
    "success":   ExecutionStatus.SUCCEEDED,
    "succeeded": ExecutionStatus.SUCCEEDED,
    "failure":   ExecutionStatus.FAILED,
    "failed":    ExecutionStatus.FAILED,
    "timeout":   ExecutionStatus.TIMED_OUT,
    "timed_out": ExecutionStatus.TIMED_OUT,
    "partial":   ExecutionStatus.FAILED,
    "cancelled": ExecutionStatus.CANCELLED,
}


def _summarize_internal_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Flatten Archon's multi-agent workflow_events into one summary.

    Per the spec: "internal traces flattened into one ExecutionResult,
    optional evidence.extensions.internal_trace_summary".
    """
    agents_used: set[str] = set()
    step_kinds: list[str] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        agent = ev.get("agent") or ev.get("role")
        if isinstance(agent, str):
            agents_used.add(agent)
        kind = ev.get("kind") or ev.get("step")
        if isinstance(kind, str):
            step_kinds.append(kind)
    return {
        "step_count": len(events or []),
        "agents_used": sorted(agents_used),
        "step_kinds": step_kinds,
    }


def normalize(raw: dict[str, Any], *, request_id: str = "", result_id: str = "") -> ExecutionResult:
    """Normalize a raw Archon workflow capture into ExecutionResult.

    Expected keys in ``raw``:
      outcome: str (success/failure/timeout/partial/cancelled)
      exit_code: int (optional)
      output_text: str
      error_text: str
      workflow_events: list[dict] — flattened to internal_trace_summary
      files_changed, commands_run, tests_run, artifacts (optional)
      Any other keys → evidence.extensions
    """
    if not isinstance(raw, dict):
        raise NormalizationError(f"raw must be a dict, got {type(raw).__name__}")

    outcome = raw.get("outcome", "failure")
    status = _OUTCOME_TO_STATUS.get(str(outcome).lower(), ExecutionStatus.FAILED)
    ok = status == ExecutionStatus.SUCCEEDED

    failure_reason: str | None = None
    if not ok:
        err = (raw.get("error_text") or "").strip()
        failure_reason = err.splitlines()[-1] if err else f"outcome={outcome}"

    workflow_events = raw.get("workflow_events", []) or []
    internal_trace_summary = _summarize_internal_trace(workflow_events)

    known_keys = {"outcome", "exit_code", "output_text", "error_text",
                  "workflow_events", "files_changed", "commands_run",
                  "tests_run", "artifacts", "summary"}
    extensions = {k: v for k, v in raw.items() if k not in known_keys}
    extensions["internal_trace_summary"] = internal_trace_summary

    evidence = Evidence(
        files_changed=list(raw.get("files_changed", [])),
        commands_run=list(raw.get("commands_run", [])),
        tests_run=list(raw.get("tests_run", [])),
        artifacts_created=list(raw.get("artifacts", [])),
        failure_reason=failure_reason,
        extensions=extensions,
    )

    summary = str(raw.get("summary", raw.get("output_text", ""))[:200])

    return ExecutionResult(
        result_id=result_id,
        request_id=request_id,
        ok=ok,
        status=status,
        summary=summary,
        evidence=evidence,
    )
