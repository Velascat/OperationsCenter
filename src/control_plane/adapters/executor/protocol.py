# src/control_plane/adapters/executor/protocol.py
"""Executor interface — the minimal contract every execution engine must satisfy.

An Executor accepts a task definition and returns a normalized result.
It is responsible for invoking the underlying tool, ensuring SwitchBoard is
used for model calls, and capturing output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class ExecutorTask:
    """Minimal task description passed to any executor.

    Attributes:
        goal:           The natural-language goal text (what to do).
        repo_path:      Absolute path to the checked-out repository.
        constraints:    Optional additional constraints / scope rules.
        metadata:       Arbitrary key-value pairs for executor-specific hints
                        (e.g. ``{"kodo_mode": "improve", "profile": "capable"}``).
    """

    goal: str
    repo_path: Path
    constraints: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutorResult:
    """Normalized result returned by every executor.

    Attributes:
        success:    True when the executor completed without error.
        output:     Combined stdout/stderr or response text from the executor.
        exit_code:  Process exit code when the executor is subprocess-based.
        executor:   Name of the executor that produced this result.
        metadata:   Executor-specific key-value pairs preserved for callers
                    (e.g. ``{"command": [...], "timeout_hit": False}``).
    """

    success: bool
    output: str
    exit_code: int | None = None
    executor: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Executor(Protocol):
    """Protocol that every executor adapter must satisfy."""

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        """Run *task* and return a normalized result."""
        ...

    def name(self) -> str:
        """Short identifier for this executor (e.g. ``"aider"`` or ``"kodo"``)."""
        ...
