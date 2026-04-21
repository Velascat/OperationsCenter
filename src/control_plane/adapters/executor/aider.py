# src/control_plane/adapters/executor/aider.py
"""AiderAdapter — runs Aider non-interactively, routing model calls through SwitchBoard.

Aider is invoked as a subprocess with:
  - ``OPENAI_API_BASE=<switchboard_url>/v1`` so all model calls go through SwitchBoard
  - ``--model openai/<profile>`` to select the SwitchBoard routing profile
  - ``--message <goal>`` for non-interactive one-shot execution
  - ``--yes`` to auto-accept all file edits

The adapter captures stdout/stderr and returns a normalised :class:`ExecutorResult`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from control_plane.adapters.executor.protocol import Executor, ExecutorResult, ExecutorTask
from control_plane.config.settings import AiderSettings


class AiderAdapter:
    """Executor adapter that delegates task execution to the Aider CLI.

    Args:
        settings:        Aider configuration (binary path, profile, timeout).
        switchboard_url: SwitchBoard base URL.  When set, ``OPENAI_API_BASE``
                         is pointed here so every Aider model call goes through
                         SwitchBoard.  Must include scheme, e.g.
                         ``"http://localhost:20401"``.
    """

    def __init__(self, settings: AiderSettings, switchboard_url: str = "") -> None:
        self._settings = settings
        self._switchboard_url = switchboard_url.rstrip("/")

    # ------------------------------------------------------------------
    # Executor protocol
    # ------------------------------------------------------------------

    def name(self) -> str:
        return "aider"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        """Run Aider on *task* and return a normalised result.

        Aider is invoked non-interactively with ``--message`` so it applies
        changes and exits without waiting for further input.
        """
        profile = task.metadata.get("profile", self._settings.profile)
        model = f"{self._settings.model_prefix}/{profile}"

        goal = task.goal
        if task.constraints:
            goal = f"{task.goal}\n\n## Constraints\n{task.constraints}"

        cmd = self._build_command(model, goal, task.repo_path)
        env = self._build_env()

        try:
            proc = subprocess.run(
                cmd,
                cwd=task.repo_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=self._settings.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ExecutorResult(
                success=False,
                output=f"[aider] Timed out after {self._settings.timeout_seconds}s",
                exit_code=-1,
                executor=self.name(),
                metadata={"command": cmd, "timeout_hit": True},
            )
        except FileNotFoundError:
            return ExecutorResult(
                success=False,
                output=f"[aider] Binary not found: {self._settings.binary}",
                exit_code=-1,
                executor=self.name(),
                metadata={"command": cmd},
            )

        output = (proc.stdout or "") + (proc.stderr or "")
        return ExecutorResult(
            success=proc.returncode == 0,
            output=output.strip(),
            exit_code=proc.returncode,
            executor=self.name(),
            metadata={
                "command": cmd,
                "model": model,
                "switchboard_url": self._switchboard_url,
            },
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_command(self, model: str, message: str, repo_path: Path) -> list[str]:
        cmd = [
            self._settings.binary,
            "--model", model,
            "--message", message,
            "--yes",
        ]
        if self._settings.model_settings_file:
            model_settings = Path(self._settings.model_settings_file)
            if model_settings.exists():
                cmd += ["--model-settings-file", str(model_settings)]
        cmd += self._settings.extra_args
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._switchboard_url:
            env["OPENAI_API_BASE"] = f"{self._switchboard_url}/v1"
        # Aider requires a non-empty API key even when using a custom base URL.
        if "OPENAI_API_KEY" not in env or not env["OPENAI_API_KEY"]:
            env["OPENAI_API_KEY"] = "sk-switchboard"
        return env
