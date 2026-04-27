"""
execution.py — ExecutionRequest, ExecutionArtifact, RunTelemetry, ExecutionResult.

These are the contracts passed between the routing layer and OperationsCenter's
execution boundary, and returned from the backend to the platform.

Flow:
  TaskProposal → (SwitchBoard) → LaneDecision
  TaskProposal + LaneDecision → (ExecutionCoordinator boundary) → ExecutionRequest
  ExecutionRequest → (backend adapter) → ExecutionResult + RunTelemetry
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .enums import ArtifactType, ExecutionStatus, FailureReasonCategory, ValidationStatus
from .common import ChangedFileRef, ValidationSummary


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# ExecutionRequest
# ---------------------------------------------------------------------------

class ExecutionRequest(BaseModel):
    """
    Everything a backend adapter needs to carry out the work.

    Produced by the OperationsCenter execution boundary after receiving a
    TaskProposal + LaneDecision. That boundary resolves execution-layer
    details (workspace, branch names) that are not present in the proposal.
    """

    run_id: str = Field(default_factory=_new_id)
    proposal_id: str = Field(description="Originating TaskProposal ID")
    decision_id: str = Field(description="Originating LaneDecision ID")

    # What to do (resolved from proposal)
    goal_text: str
    constraints_text: Optional[str] = None

    # Where (resolved at runtime)
    repo_key: str
    clone_url: str
    base_branch: str
    task_branch: str = Field(description="Branch created for this run")
    workspace_path: Path = Field(description="Absolute path to checked-out workspace")
    goal_file_path: Optional[Path] = Field(
        default=None,
        description="Path to the goal file written inside the workspace",
    )

    # Constraints (from proposal, propagated)
    allowed_paths: list[str] = Field(default_factory=list)
    max_changed_files: Optional[int] = None
    timeout_seconds: int = Field(default=300, ge=1)
    require_clean_validation: bool = True
    validation_commands: list[str] = Field(default_factory=list)

    # Metadata
    requested_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# ExecutionArtifact
# ---------------------------------------------------------------------------

class ExecutionArtifact(BaseModel):
    """
    A discrete artifact produced during execution — diff, report, log excerpt, etc.

    Backends attach these to ExecutionResult. Consumers may use them for display,
    audit, or further processing.
    """

    artifact_id: str = Field(default_factory=_new_id)
    artifact_type: ArtifactType
    label: str = Field(description="Short human-readable label, e.g. 'pre-merge diff'")
    content: Optional[str] = Field(
        default=None,
        description="Inline content for small artifacts (diffs, excerpts).",
    )
    uri: Optional[str] = Field(
        default=None,
        description="URI for large artifacts stored externally.",
    )
    size_bytes: Optional[int] = Field(default=None, ge=0)
    produced_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# RunTelemetry
# ---------------------------------------------------------------------------

class RunTelemetry(BaseModel):
    """
    Timing, resource, and diagnostic data for one execution run.

    Separating telemetry from ExecutionResult keeps the result model clean
    for routing/retry decisions while preserving full observability data.
    """

    run_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)

    # Backend-reported counts
    llm_calls: int = Field(default=0, ge=0)
    llm_input_tokens: int = Field(default=0, ge=0)
    llm_output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)

    # Execution path
    lane_name: Optional[str] = None
    backend_name: Optional[str] = None
    backend_version: Optional[str] = None

    # Free-form labels the backend wants to attach
    labels: dict[str, str] = Field(default_factory=dict)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------

class ExecutionResult(BaseModel):
    """
    The canonical outcome of an execution run.

    Produced by the backend adapter and returned to the OperationsCenter execution
    boundary / platform. This is backend-agnostic: a kodo adapter, Archon
    adapter, and any future adapter all return this same shape.
    """

    run_id: str
    proposal_id: str
    decision_id: str

    # Outcome
    status: ExecutionStatus
    success: bool = Field(description="True only when status == SUCCESS")

    # What changed
    changed_files: list[ChangedFileRef] = Field(default_factory=list)
    changed_files_source: Optional[str] = Field(
        default=None,
        description=(
            "How changed-file evidence was obtained: git_diff, backend_manifest, "
            "event_stream, backend_confirmed_empty, unknown, or similar."
        ),
    )
    changed_files_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence in changed-file evidence provenance. "
            "Adapters should set this explicitly when they know the source."
        ),
    )
    diff_stat_excerpt: Optional[str] = Field(
        default=None,
        description="Short summary of the diff (e.g. '3 files changed, 47 insertions(+)')",
    )

    # Validation
    validation: ValidationSummary = Field(
        default_factory=lambda: ValidationSummary(status=ValidationStatus.SKIPPED),
    )

    # Branch / PR
    branch_pushed: bool = False
    branch_name: Optional[str] = None
    pull_request_url: Optional[str] = None

    # Failure detail
    failure_category: Optional[FailureReasonCategory] = None
    failure_reason: Optional[str] = Field(
        default=None,
        description="Human-readable explanation of why the run failed",
    )

    # Artifacts
    artifacts: list[ExecutionArtifact] = Field(default_factory=list)

    # Metadata
    completed_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}
