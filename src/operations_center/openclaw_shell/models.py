# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
openclaw_shell/models.py — shell-facing models for the OpenClaw outer shell.

These models are concise, operator-friendly views of the internal state.
They are derived from canonical contracts and internal observability records.
They do not replace or redefine the canonical contract layer.

Design rules:
- Frozen Pydantic models for all shell-facing outputs (JSON-serializable)
- OperatorContext is a plain dataclass (input, not a contract)
- All output models derive from canonical LaneDecision / ExecutionResult /
  ExecutionRecord / ExecutionTrace — never from OpenClaw-native event streams
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Shell input
# ---------------------------------------------------------------------------


@dataclass
class OperatorContext:
    """Shell-level input describing an operator's intent for a run.

    Thin operator-facing type. Not a canonical contract. The shell bridge
    maps this to a PlanningContext before calling into internal services.

    Fields intentionally mirror PlanningContext at the operator level —
    they use plain strings rather than enum values to keep the shell
    interface ergonomic.
    """

    goal_text: str
    repo_key: str

    # Task framing
    task_type: str = "goal"           # e.g. "lint_fix", "bug_fix", "refactor"
    execution_mode: str = "goal"      # e.g. "goal", "fix_pr"

    # Where
    clone_url: str = ""
    base_branch: str = "main"

    # Risk and priority
    risk_level: str = "low"           # "low" | "medium" | "high"
    priority: str = "normal"          # "low" | "normal" | "high" | "critical"

    # Optional constraints
    constraints_text: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    timeout_seconds: int = 300

    # Shell-level flags (not passed to internal services)
    shell_flags: dict = field(default_factory=dict)

    # Provenance
    task_id: str = ""
    project_id: str = ""


# ---------------------------------------------------------------------------
# Shell outputs — derived from canonical internal data
# ---------------------------------------------------------------------------


class ShellRunHandle(BaseModel):
    """Handle returned when a run is triggered through the shell.

    Represents a planned-but-not-yet-executing run. Contains routing
    context derived from the ProposalDecisionBundle.

    This is NOT an ExecutionRequest. Execution has not started.
    The handle provides what the operator needs to reference the run
    and understand the routing intent.
    """

    handle_id: str = Field(default_factory=_new_id)
    proposal_id: str
    decision_id: str
    selected_lane: str = Field(description="LaneName value from routing decision")
    selected_backend: str = Field(description="BackendName from routing decision")
    routing_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    policy_rule: Optional[str] = None
    status: str = Field(default="planned", description="Shell-level run status")
    summary: str = Field(default="", description="Human-readable summary from bundle")
    created_at: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}


class ShellStatusSummary(BaseModel):
    """Shell-friendly status summary derived from a canonical ExecutionResult
    or ExecutionRecord/ExecutionTrace.

    Does not invent new truth — all fields come from internal normalized data.
    """

    run_id: str
    proposal_id: str
    decision_id: str
    status: str = Field(description="ExecutionStatus value")
    success: bool
    headline: str = Field(description="One-line summary from ExecutionTrace")
    summary: str = Field(description="Multi-line summary from ExecutionTrace")
    selected_lane: Optional[str] = None
    selected_backend: Optional[str] = None
    changed_files_status: str = Field(default="unknown")
    validation_status: str = Field(default="skipped")
    artifact_count: int = Field(default=0, ge=0)
    recorded_at: Optional[datetime] = None

    model_config = {"frozen": True}


class ShellInspectionResult(BaseModel):
    """Detailed shell-facing inspection derived from ExecutionRecord + ExecutionTrace.

    Gives the operator a complete picture of a retained run without exposing
    backend-native internals. All fields derive from the observability layer.
    """

    run_id: str
    proposal_id: str
    decision_id: str
    status: str
    headline: str
    summary: str
    warnings: list[str] = Field(default_factory=list)
    artifact_count: int = 0
    primary_artifact_count: int = 0
    changed_files_status: str = "unknown"
    validation_status: str = "skipped"
    backend_detail_count: int = 0
    selected_lane: Optional[str] = None
    selected_backend: Optional[str] = None
    trace_id: Optional[str] = None
    record_id: Optional[str] = None
    recorded_at: Optional[datetime] = None

    model_config = {"frozen": True}


class ShellActionResult(BaseModel):
    """Generic action result for shell operations that may succeed or fail.

    Used for operations that have a clear outcome but no specific domain result.
    """

    action: str = Field(description="Name of the action performed")
    success: bool
    message: str = ""
    detail: Optional[str] = None

    model_config = {"frozen": True}
