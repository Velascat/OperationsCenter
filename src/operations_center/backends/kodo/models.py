# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/kodo/models.py — kodo-specific internal models.

These models are quarantined inside the kodo backend namespace.
They must not replace or escape into canonical contract types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class KodoPreparedRun:
    """Everything kodo needs to execute one task.

    Produced by KodoMapper from a canonical ExecutionRequest.
    Consumed by KodoBackendInvoker.
    """

    run_id: str
    goal_text: str
    constraints_text: Optional[str]
    repo_path: Path
    task_branch: str
    goal_file_path: Path
    validation_commands: list[str]
    timeout_seconds: int
    kodo_mode: str = "goal"
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class KodoArtifactCapture:
    """A single artifact captured from a kodo run."""

    label: str
    content: str
    artifact_type: str  # mirrors ArtifactType values


@dataclass
class KodoRunCapture:
    """Raw outputs captured from a kodo invocation.

    Produced by KodoBackendInvoker.
    Consumed by KodoNormalizer.
    """

    run_id: str
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    timeout_hit: bool = False
    rate_limited: bool = False
    quota_exhausted: bool = False
    artifacts: list[KodoArtifactCapture] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    @property
    def combined_output(self) -> str:
        return ((self.stdout or "") + "\n" + (self.stderr or "")).strip()


@dataclass
class KodoFailureInfo:
    """Structured failure detail extracted from a kodo capture."""
    exit_code: int
    failure_category_value: str  # matches FailureReasonCategory values
    failure_reason: str
    is_timeout: bool = False
    is_rate_limited: bool = False
    is_quota_exhausted: bool = False


@dataclass
class SupportCheck:
    """Whether a canonical ExecutionRequest is suitable for kodo execution."""

    supported: bool
    reason: Optional[str] = None
    unsupported_fields: list[str] = field(default_factory=list)

    @classmethod
    def yes(cls) -> "SupportCheck":
        return cls(supported=True)

    @classmethod
    def no(cls, reason: str, fields: list[str] | None = None) -> "SupportCheck":
        return cls(supported=False, reason=reason, unsupported_fields=fields or [])
