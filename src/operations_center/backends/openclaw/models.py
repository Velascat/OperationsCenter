# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/openclaw/models.py — OpenClaw-specific internal models.

All models in this file are quarantined inside the OpenClaw backend namespace.
They must not replace or escape into canonical contract types.

The key addition compared to other adapters is explicit changed-file evidence
tracking. OpenClaw may or may not report changed files directly; the source
of that information must be represented honestly.

changed_files_source values:
  "git_diff"     — authoritative, discovered via git diff on workspace
  "event_stream" — inferred, extracted from OpenClaw event stream
  "unknown"      — no reliable source; changed_files will be empty
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional


@dataclass
class SupportCheck:
    """Whether a canonical ExecutionRequest is suitable for OpenClaw execution."""

    supported: bool
    reason: Optional[str] = None
    unsupported_fields: list[str] = field(default_factory=list)

    @classmethod
    def yes(cls) -> "SupportCheck":
        return cls(supported=True)

    @classmethod
    def no(cls, reason: str, fields: list[str] | None = None) -> "SupportCheck":
        return cls(supported=False, reason=reason, unsupported_fields=fields or [])


@dataclass
class OpenClawPreparedRun:
    """Everything OpenClaw needs to execute one task.

    Produced by the mapper from a canonical ExecutionRequest.
    Consumed by OpenClawBackendInvoker.

    run_mode maps the canonical execution mode: "goal" (default), "fix_pr",
    "test", "improve".
    """

    run_id: str
    goal_text: str
    constraints_text: Optional[str]
    repo_path: Path
    task_branch: str
    run_mode: str = "goal"
    timeout_seconds: int = 300
    validation_commands: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class OpenClawArtifactCapture:
    """A single artifact captured from an OpenClaw run."""

    label: str
    content: str
    artifact_type: str  # mirrors ArtifactType values



@dataclass
class OpenClawRunCapture:
    """Raw outputs captured from an OpenClaw invocation.

    Produced by OpenClawBackendInvoker. Consumed by the normalizer.

    events holds the raw OpenClaw event stream. It is NOT inlined into
    canonical telemetry — callers may retain it via BackendDetailRef if needed.

    reported_changed_files is the list of changed files OpenClaw itself
    reported in its event stream or output. It is present only when OpenClaw
    explicitly surfaces this information. changed_files_source records where
    the changed-file list in the final ExecutionResult came from.
    """

    run_id: str
    outcome: str          # "success", "failure", "timeout", "partial"
    exit_code: int
    output_text: str
    error_text: str
    events: list[dict] = field(default_factory=list)
    artifacts: list[OpenClawArtifactCapture] = field(default_factory=list)
    reported_changed_files: list[dict] = field(default_factory=list)
    changed_files_source: str = "unknown"  # "git_diff" | "event_stream" | "unknown"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = 0
    timeout_hit: bool = False

    @property
    def succeeded(self) -> bool:
        return self.outcome == "success"

    @property
    def combined_output(self) -> str:
        return ((self.output_text or "") + "\n" + (self.error_text or "")).strip()

    @property
    def event_count(self) -> int:
        return len(self.events)


@dataclass
class OpenClawFailureInfo:
    """Structured failure detail extracted from an OpenClaw capture."""
    outcome: str
    failure_category_value: str  # matches FailureReasonCategory values
    failure_reason: str
    is_timeout: bool = False
    is_partial: bool = False


@dataclass
class OpenClawEventDetailRef:
    """Reference to a chunk of raw OpenClaw event stream data.

    Retained for observability (BackendDetailRef) without contaminating
    canonical ExecutionResult. Not inlined into canonical telemetry.
    """
    event_type: str  # e.g. "tool_use", "tool_result", "message", "step"
    index: int
    summary: str = ""  # brief human-readable description

