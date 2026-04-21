# src/control_plane/adapters/executor/kodo.py
"""KodoExecutorAdapter — wraps the existing KodoAdapter behind the Executor interface.

This adapter bridges :class:`ExecutorTask` → Kodo's ``write_goal_file`` / ``run()``
and converts :class:`KodoRunResult` → :class:`ExecutorResult`.

When ``switchboard_url`` is set, ``OPENAI_API_BASE`` is injected into the Kodo
subprocess environment so that any OpenAI-compatible worker agents Kodo spawns
(team ``full`` codex workers) route their model calls through SwitchBoard.

Note: Kodo's *orchestrator* (Claude Code CLI) does not use ``OPENAI_API_BASE``
and does not route through SwitchBoard.  Only worker agents using the OpenAI
backend are affected.  This limitation is documented in ``docs/phase6.md``.
"""
from __future__ import annotations

import os

from control_plane.adapters.executor.protocol import ExecutorResult, ExecutorTask
from control_plane.adapters.kodo.adapter import KodoAdapter, KodoRunResult
from control_plane.config.settings import KodoSettings


class KodoExecutorAdapter:
    """Executor adapter that delegates to Kodo via the existing :class:`KodoAdapter`.

    Args:
        kodo:            The underlying KodoAdapter instance.
        switchboard_url: SwitchBoard base URL.  When set, ``OPENAI_API_BASE``
                         is added to Kodo's subprocess environment so OpenAI
                         worker agents route through SwitchBoard.
    """

    def __init__(self, kodo: KodoAdapter, switchboard_url: str = "") -> None:
        self._kodo = kodo
        self._switchboard_url = switchboard_url.rstrip("/")

    # ------------------------------------------------------------------
    # Executor protocol
    # ------------------------------------------------------------------

    def name(self) -> str:
        return "kodo"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        """Write goal file and invoke Kodo, returning a normalised result."""
        kodo_mode: str = task.metadata.get("kodo_mode", "goal")
        profile: KodoSettings | None = task.metadata.get("kodo_profile")

        # Write goal file into repo (Kodo reads this from disk)
        goal_file = task.repo_path / ".kodo_goal.md"
        self._kodo.write_goal_file(goal_file, task.goal, task.constraints or None)

        env = self._build_env()

        try:
            result = self._kodo.run(
                goal_file,
                task.repo_path,
                env=env,
                profile=profile,
                kodo_mode=kodo_mode,
            )
        finally:
            goal_file.unlink(missing_ok=True)

        return self._to_result(result)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._switchboard_url:
            env["OPENAI_API_BASE"] = f"{self._switchboard_url}/v1"
        return env

    def _to_result(self, result: KodoRunResult) -> ExecutorResult:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        is_timeout = "[timeout:" in (result.stderr or "")
        is_rate_limited = KodoAdapter.is_orchestrator_rate_limited(result)
        return ExecutorResult(
            success=result.exit_code == 0,
            output=output,
            exit_code=result.exit_code,
            executor=self.name(),
            metadata={
                "command": result.command,
                "timeout_hit": is_timeout,
                "rate_limited": is_rate_limited,
                "switchboard_url": self._switchboard_url,
            },
        )
