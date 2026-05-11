# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
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

from cxrp.contracts.runtime_binding import RuntimeBinding as CxrpRuntimeBinding
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

    # Recovery loop signal — see execution/recovery_loop/. Defaults to False
    # because most backend adapter calls have side effects (file writes, git
    # commits, branch pushes, PR creation). Callers that produce a side-effect-
    # free request may set ``idempotent=True`` to opt in to retry on transient
    # failures.
    idempotent: bool = False

    # Runtime binding — what powers the executor for this request.
    # ``RuntimeBindingSummary`` remains the OC-facing import name for
    # compatibility, but the concrete type is the canonical CxRP
    # RuntimeBinding rather than a local mirror.
    runtime_binding: Optional["RuntimeBindingSummary"] = None

    # Phase 6 — the strict, validated execution target this request was
    # bound to. Forward-ref'd as a string to avoid the import cycle
    # (BoundExecutionTarget references RuntimeBindingSummary in turn).
    bound_target: Optional["BoundExecutionTargetMirror"] = None

    # ER-003 — optional lifecycle metadata. Absence preserves one-shot
    # behavior; presence drives operations_center.lifecycle.LifecycleRunner.
    lifecycle: Optional["LifecycleMetadata"] = None

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
    success: bool = Field(description="True only when status == SUCCEEDED")

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

    # Recovery audit trail — populated by ExecutionCoordinator when the
    # bounded recovery loop runs. None on legacy results / single-attempt
    # paths that never engaged the loop. See execution/recovery_loop/.
    recovery: Optional["RecoveryMetadataSummary"] = None

    # G-V01 — pointer back to the RxP RuntimeInvocation/RuntimeResult that
    # powered this run. Adapters that delegate to ExecutorRuntime populate
    # this so an OC ExecutionResult can be linked to RxP runtime artifacts
    # (stdout/stderr/artifact_directory). Adapters that do not invoke a
    # runtime (e.g. demo_stub) leave this None.
    runtime_invocation_ref: Optional["RuntimeInvocationRef"] = None

    # Phase 6 — the BoundExecutionTarget the coordinator used for this
    # run. Recorded for audit/replay so any past run can answer:
    # which lane was requested? which backend executed? which runtime
    # powered it? which fork/ref/patches were actually used?
    # See docs/architecture/contracts/execution_target.md.
    bound_target: Optional["BoundExecutionTargetMirror"] = None

    # ER-003 — lifecycle outcome attached when the request carried lifecycle
    # metadata. Reports completed/failed/skipped stages.
    lifecycle_outcome: Optional["LifecycleOutcome"] = None

    # Metadata
    completed_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Recovery metadata (mirror of recovery_loop.RecoveryMetadata, kept in the
# contracts layer to avoid a circular dependency between contracts and the
# recovery_loop package). Enum values are stored as plain strings here so
# the contract does not depend on recovery_loop's enum classes.
# ---------------------------------------------------------------------------


class RecoveryActionSummary(BaseModel):
    """One recorded recovery decision for a single attempt."""

    attempt: int = Field(ge=1)
    failure_kind: str
    decision: str
    reason: str
    handler_name: Optional[str] = None
    modified_fields: list[str] = Field(default_factory=list)
    delay_seconds: Optional[float] = None
    executor_exit_code: Optional[int] = None
    executor_signal: Optional[str] = None
    retry_strategy_used: Optional[str] = None
    retry_strategy_changed: Optional[bool] = None
    remediation_attempt_number: Optional[int] = None
    remediation_lineage_id: Optional[str] = None
    prior_failure_signature: Optional[str] = None

    model_config = {"frozen": True}


class RecoveryMetadataSummary(BaseModel):
    """Recovery audit trail attached to ``ExecutionResult.recovery``."""

    attempts: int = Field(ge=1)
    actions: list[RecoveryActionSummary] = Field(default_factory=list)
    final_decision: str
    retry_refused_reason: Optional[str] = None

    model_config = {"frozen": True}


class BackendProvenanceMirror(BaseModel):
    """Contract-layer mirror of execution.target.BackendProvenance.

    Same import-cycle-avoiding pattern as RuntimeBindingSummary: the
    contract layer can't import the execution layer. ExecutionRequest
    carries the mirror; coordinator code converts to/from the real
    BackendProvenance at the boundary.
    """

    source: str = "unknown"
    repo: Optional[str] = None
    ref: Optional[str] = None
    patches: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class BoundExecutionTargetMirror(BaseModel):
    """Contract-layer mirror of execution.target.BoundExecutionTarget."""

    lane: str
    backend: str
    executor: Optional[str] = None
    runtime_binding: Optional["RuntimeBindingSummary"] = None
    provenance: Optional[BackendProvenanceMirror] = None

    model_config = {"frozen": True}


class RuntimeInvocationRef(BaseModel):
    """Link from an OC ExecutionResult to the RxP RuntimeInvocation/Result that produced it.

    Populated by adapters that delegate execution mechanics to
    ExecutorRuntime. Carries the identity of the RuntimeInvocation
    (``invocation_id``) plus the ExecutorRuntime-captured stdout/stderr
    paths and the per-call artifact directory, so audit/replay can reach
    the underlying RxP RuntimeResult artifacts from the OC result alone.
    """

    invocation_id: str = Field(description="RuntimeInvocation.invocation_id (matches RuntimeResult.invocation_id)")
    runtime_name: str = Field(description="Logical runtime name passed to ExecutorRuntime, e.g. 'direct_local', 'kodo'")
    runtime_kind: str = Field(description="RxP runtime kind, e.g. 'subprocess', 'http_async', 'manual'")
    stdout_path: Optional[str] = Field(default=None, description="RuntimeResult.stdout_path, if the runner captured it")
    stderr_path: Optional[str] = Field(default=None, description="RuntimeResult.stderr_path, if the runner captured it")
    artifact_directory: Optional[str] = Field(
        default=None,
        description="RuntimeInvocation.artifact_directory used for this call, when set by the adapter",
    )

    model_config = {"frozen": True}


# Backward-compatible import surface: most OC code still imports
# ``RuntimeBindingSummary`` from this module, but the concrete type is
# the canonical CxRP RuntimeBinding rather than a local contract mirror.
RuntimeBindingSummary = CxrpRuntimeBinding


# ER-003 — pull lifecycle types into local scope so the forward refs on
# ExecutionRequest.lifecycle and ExecutionResult.lifecycle_outcome resolve.
from operations_center.lifecycle.models import (  # noqa: E402,F401
    LifecycleMetadata,
    LifecycleOutcome,
)

# Resolve forward references now that the summary types are defined.
ExecutionResult.model_rebuild()
ExecutionRequest.model_rebuild()
BoundExecutionTargetMirror.model_rebuild()
