# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/archon/mapper.py — maps canonical ExecutionRequest → ArchonWorkflowConfig.

The mapper is a pure function. It does not invoke Archon, write files, or
touch the filesystem. Those responsibilities belong to the invoker.

workflow_type is derived from the request's execution_mode hint where present
in metadata. Callers may override by passing workflow_type explicitly.
"""

from __future__ import annotations

from pathlib import Path

from operations_center.contracts.execution import ExecutionRequest

from .models import ArchonWorkflowConfig, SupportCheck


# Map canonical execution mode names to Archon workflow types.
_EXECUTION_MODE_TO_WORKFLOW_TYPE: dict[str, str] = {
    "goal": "goal",
    "fix_pr": "fix_pr",
    "test_campaign": "test",
    "improve_campaign": "improve",
}


def check_support(request: ExecutionRequest) -> SupportCheck:
    """Return a SupportCheck indicating whether Archon can handle this request."""
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
    workflow_type: str = "goal",
) -> ArchonWorkflowConfig:
    """Map a canonical ExecutionRequest into an ArchonWorkflowConfig.

    Args:
        request:       The canonical ExecutionRequest to map.
        workflow_type: Archon workflow strategy — 'goal' (default), 'fix_pr',
                       'test', or 'improve'. Derived from execution mode when
                       not supplied explicitly.

    Raises:
        ValueError: if the request is not supported by Archon.
    """
    check = check_support(request)
    if not check.supported:
        raise ValueError(f"ExecutionRequest not suitable for Archon: {check.reason}")

    resolved_workflow_type = _resolve_workflow_type(request, workflow_type)

    metadata = {
        "proposal_id": request.proposal_id,
        "decision_id": request.decision_id,
        "task_branch": request.task_branch,
        "base_branch": request.base_branch,
        "clone_url": request.clone_url,
    }
    if request.allowed_paths:
        metadata["allowed_paths"] = ",".join(request.allowed_paths)

    return ArchonWorkflowConfig(
        run_id=request.run_id,
        goal_text=request.goal_text,
        constraints_text=request.constraints_text or None,
        repo_path=Path(request.workspace_path),
        task_branch=request.task_branch,
        workflow_type=resolved_workflow_type,
        timeout_seconds=request.timeout_seconds,
        validation_commands=list(request.validation_commands),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_workflow_type(request: ExecutionRequest, explicit: str) -> str:
    """Resolve workflow type from explicit arg, falling back to execution mode."""
    if explicit != "goal":
        return explicit
    mode_hint = getattr(request, "execution_mode_hint", None)
    if mode_hint:
        return _EXECUTION_MODE_TO_WORKFLOW_TYPE.get(mode_hint, "goal")
    return explicit
