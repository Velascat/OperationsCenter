"""
backends/archon/models.py — Archon-specific internal models.

All models in this file are quarantined inside the Archon backend namespace.
They must not replace or escape into canonical contract types.

The key distinction from the kodo models is the workflow-oriented shape:
ArchonWorkflowConfig carries a workflow_type and structured metadata;
ArchonRunCapture includes workflow_events for raw event retention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class SupportCheck:
    """Whether a canonical ExecutionRequest is suitable for Archon execution."""

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
class ArchonWorkflowConfig:
    """Everything Archon needs to execute one workflow task.

    Produced by ArchonMapper from a canonical ExecutionRequest.
    Consumed by ArchonBackendInvoker.

    workflow_type maps the canonical execution mode to an Archon-compatible
    workflow strategy: "goal" (default), "fix_pr", "test", "improve".
    """

    run_id: str
    goal_text: str
    constraints_text: Optional[str]
    repo_path: Path
    task_branch: str
    workflow_type: str = "goal"
    timeout_seconds: int = 300
    validation_commands: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class ArchonArtifactCapture:
    """A single artifact captured from an Archon run."""

    label: str
    content: str
    artifact_type: str  # mirrors ArtifactType values


@dataclass
class ArchonRunCapture:
    """Raw outputs captured from an Archon invocation.

    Produced by ArchonBackendInvoker.
    Consumed by the Archon normalizer.

    workflow_events holds raw Archon event/step data. It is NOT inlined into
    canonical telemetry — callers may retain it via BackendDetailRef if needed.
    The normalizer extracts only a structured summary for canonical use.
    """

    run_id: str
    outcome: str  # "success", "failure", "timeout", "partial"
    exit_code: int
    output_text: str
    error_text: str
    workflow_events: list[dict] = field(default_factory=list)
    artifacts: list[ArchonArtifactCapture] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.utcnow())
    finished_at: datetime = field(default_factory=lambda: datetime.utcnow())
    duration_ms: int = 0
    timeout_hit: bool = False

    @property
    def succeeded(self) -> bool:
        return self.outcome == "success"

    @property
    def combined_output(self) -> str:
        return ((self.output_text or "") + "\n" + (self.error_text or "")).strip()


@dataclass
class ArchonFailureInfo:
    """Structured failure detail extracted from an Archon capture."""

    outcome: str
    failure_category_value: str  # matches FailureReasonCategory values
    failure_reason: str
    is_timeout: bool = False
    is_partial: bool = False
