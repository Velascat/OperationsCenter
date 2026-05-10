# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/openclaw/mapper.py — maps canonical ExecutionRequest → OpenClawPreparedRun.

The mapper is a pure function. It does not invoke OpenClaw, write files, or
touch the filesystem. Those responsibilities belong to the invoker.

run_mode is derived from the request's execution mode hint where present in
metadata. Callers may override by passing run_mode explicitly.

Changed-file evidence is not determined at mapping time — that happens during
invocation and normalization.
"""

from __future__ import annotations

from pathlib import Path

from operations_center.contracts.execution import ExecutionRequest

from .models import OpenClawPreparedRun, SupportCheck


# Map canonical execution mode names to OpenClaw run modes.
_EXECUTION_MODE_TO_RUN_MODE: dict[str, str] = {
    "goal": "goal",
    "fix_pr": "fix_pr",
    "test_campaign": "test",
    "improve_campaign": "improve",
}


def check_support(request: ExecutionRequest) -> SupportCheck:
    """Return a SupportCheck indicating whether OpenClaw can handle this request.

    Checks:
    - goal_text must be non-empty
    - repo_key must be non-empty
    - workspace_path must not be empty (Path("") normalizes to Path("."))
    """
    issues: list[str] = []

    if not request.repo_key:
        issues.append("repo_key")
    if str(request.workspace_path) in ("", "."):
        issues.append("workspace_path")
    if not request.goal_text.strip():
        issues.append("goal_text")

    if issues:
        return SupportCheck.no(
            reason=f"Required fields missing or empty: {', '.join(issues)}",
            fields=issues,
        )

    return SupportCheck.yes()


def map_request(
    request: ExecutionRequest,
    run_mode: str = "goal",
) -> OpenClawPreparedRun:
    """Map a canonical ExecutionRequest into an OpenClawPreparedRun.

    Args:
        request:  The canonical ExecutionRequest to map.
        run_mode: OpenClaw execution mode — 'goal' (default), 'fix_pr',
                  'test', or 'improve'. Derived from execution mode hint
                  when not supplied explicitly.

    Raises:
        ValueError: if the request is not suitable for OpenClaw.
    """
    check = check_support(request)
    if not check.supported:
        raise ValueError(f"ExecutionRequest not suitable for OpenClaw: {check.reason}")

    resolved_mode = _resolve_run_mode(request, run_mode)

    metadata = {
        "proposal_id": request.proposal_id,
        "decision_id": request.decision_id,
        "task_branch": request.task_branch,
        "base_branch": request.base_branch,
        "clone_url": request.clone_url,
    }
    if request.allowed_paths:
        metadata["allowed_paths"] = ",".join(request.allowed_paths)

    return OpenClawPreparedRun(
        run_id=request.run_id,
        goal_text=request.goal_text,
        constraints_text=request.constraints_text or None,
        repo_path=Path(request.workspace_path),
        task_branch=request.task_branch,
        run_mode=resolved_mode,
        timeout_seconds=request.timeout_seconds,
        validation_commands=list(request.validation_commands),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_run_mode(request: ExecutionRequest, explicit: str) -> str:
    """Resolve run mode from explicit arg, falling back to execution mode hint."""
    if explicit != "goal":
        return explicit
    mode_hint = getattr(request, "execution_mode_hint", None)
    if mode_hint:
        return _EXECUTION_MODE_TO_RUN_MODE.get(mode_hint, "goal")
    return explicit
