# src/control_plane/adapters/executor/factory.py
"""ExecutorFactory — creates executor adapters from configuration.

Selection is config-driven and deterministic:
  - Per-repo ``executor`` field in ``RepoSettings`` (default: ``"kodo"``)
  - Top-level ``AiderSettings`` supplies Aider binary + options
  - ``SWITCHBOARD_URL`` env var (or ``spec_director.switchboard_url``) routes
    model calls through SwitchBoard for both adapters

Usage:
    executor = ExecutorFactory.create("aider", settings)
    result = executor.execute(task)
"""
from __future__ import annotations

import os

from control_plane.adapters.executor.aider import AiderAdapter
from control_plane.adapters.executor.kodo import KodoExecutorAdapter
from control_plane.adapters.executor.protocol import Executor
from control_plane.adapters.kodo.adapter import KodoAdapter
from control_plane.config.settings import Settings


class ExecutorFactory:
    """Creates executor adapters from application settings."""

    @staticmethod
    def create(executor_type: str, settings: Settings) -> Executor:
        """Instantiate and return an executor for *executor_type*.

        Args:
            executor_type:  ``"aider"`` or ``"kodo"`` (case-insensitive).
            settings:       Full application settings.

        Returns:
            An :class:`Executor` instance ready to call ``.execute()``.

        Raises:
            ValueError: For unknown executor types.
        """
        switchboard_url = ExecutorFactory._resolve_switchboard_url(settings)
        kind = executor_type.lower()

        if kind == "aider":
            return AiderAdapter(settings.aider, switchboard_url=switchboard_url)

        if kind == "kodo":
            kodo = KodoAdapter(settings.kodo)
            return KodoExecutorAdapter(kodo, switchboard_url=switchboard_url)

        raise ValueError(
            f"Unknown executor type: {executor_type!r}. Valid options: 'aider', 'kodo'."
        )

    @staticmethod
    def for_repo(repo_key: str, settings: Settings) -> Executor:
        """Create the executor configured for *repo_key*.

        Falls back to ``"kodo"`` when the repo has no explicit executor field.
        """
        repo_cfg = settings.repos.get(repo_key)
        executor_type = getattr(repo_cfg, "executor", "kodo") or "kodo"
        return ExecutorFactory.create(executor_type, settings)

    @staticmethod
    def _resolve_switchboard_url(settings: Settings) -> str:
        """Return the SwitchBoard URL from env or settings (env takes precedence)."""
        from_env = os.environ.get("SWITCHBOARD_URL", "")
        if from_env:
            return from_env
        sd_url = getattr(settings.spec_director, "switchboard_url", None)
        return sd_url or ""
