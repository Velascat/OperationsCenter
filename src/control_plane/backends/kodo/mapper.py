"""
backends/kodo/mapper.py — maps canonical ExecutionRequest → KodoPreparedRun.

The mapper is a pure function. It does not invoke kodo, write files, or
touch the filesystem. Those responsibilities belong to the invoker.
"""

from __future__ import annotations

from pathlib import Path

from control_plane.contracts.execution import ExecutionRequest

from .models import KodoPreparedRun, SupportCheck


def check_support(request: ExecutionRequest) -> SupportCheck:
    """Return a SupportCheck indicating whether kodo can handle this request."""
    issues: list[str] = []

    if not request.repo_key:
        issues.append("repo_key")
    if not request.workspace_path:
        issues.append("workspace_path")
    if not request.goal_text.strip():
        issues.append("goal_text")

    if issues:
        return SupportCheck.no(
            reason=f"Required fields missing or empty: {', '.join(issues)}",
            fields=issues,
        )

    return SupportCheck.yes()


def map_request(request: ExecutionRequest, kodo_mode: str = "goal") -> KodoPreparedRun:
    """Map a canonical ExecutionRequest into a KodoPreparedRun.

    Args:
        request:    The canonical ExecutionRequest to map.
        kodo_mode:  kodo invocation mode — 'goal' (default), 'test', or 'improve'.
                    Callers that know the execution strategy should pass this explicitly.

    Raises:
        ValueError: if the request is not supported by kodo.
    """
    check = check_support(request)
    if not check.supported:
        raise ValueError(f"ExecutionRequest not suitable for kodo: {check.reason}")

    goal_file_path = _resolve_goal_file_path(request)

    return KodoPreparedRun(
        run_id=request.run_id,
        goal_text=request.goal_text,
        constraints_text=request.constraints_text or None,
        repo_path=Path(request.workspace_path),
        task_branch=request.task_branch,
        goal_file_path=goal_file_path,
        validation_commands=list(request.validation_commands),
        timeout_seconds=request.timeout_seconds,
        kodo_mode=kodo_mode,
        env_overrides={},
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_goal_file_path(request: ExecutionRequest) -> Path:
    if request.goal_file_path:
        return Path(request.goal_file_path)
    return Path(request.workspace_path) / ".kodo_goal.md"
