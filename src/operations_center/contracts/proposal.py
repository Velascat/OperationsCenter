# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
proposal.py — TaskProposal: the canonical proposal emitted by OperationsCenter.

A TaskProposal is the decision that a task is worth attempting. It carries
everything needed to route the task (via SwitchBoard) and execute it through
OperationsCenter's execution boundary. It does not contain execution-layer
internals (workspace paths, branch names) — those are resolved at execution
time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .enums import ExecutionMode, Priority, RiskLevel, TaskType
from .common import BranchPolicy, ExecutionConstraints, TaskTarget, ValidationProfile


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class TaskProposal(BaseModel):
    """
    A task proposed by OperationsCenter for execution.

    SwitchBoard consumes this to select a lane. OperationsCenter's execution
    boundary consumes it to understand what to do. Neither component should
    need to reach back into planning internals to execute the task.
    """

    # Identity
    proposal_id: str = Field(
        default_factory=_new_id,
        description="Unique identifier for this proposal.",
    )
    task_id: str = Field(description="Upstream task identifier (e.g. Plane board task ID)")
    project_id: str = Field(description="Project or board the task belongs to")

    # What to do
    task_type: TaskType = Field(description="Broad category of the proposed work")
    execution_mode: ExecutionMode = Field(description="Execution strategy for the run")
    goal_text: str = Field(description="Natural-language description of what to accomplish")
    constraints_text: Optional[str] = Field(
        default=None,
        description="Natural-language constraints or restrictions on the change",
    )

    # Where to do it
    target: TaskTarget = Field(description="Repository and branch context")

    # How to do it
    priority: Priority = Field(default=Priority.NORMAL)
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW,
        description="OperationsCenter's estimate of change risk — used by SwitchBoard routing policy",
    )
    constraints: ExecutionConstraints = Field(default_factory=ExecutionConstraints)
    validation_profile: ValidationProfile = Field(
        default_factory=lambda: ValidationProfile(profile_name="default"),
        description="Validation commands to run after execution",
    )
    branch_policy: BranchPolicy = Field(default_factory=BranchPolicy)

    # Metadata
    proposed_at: datetime = Field(default_factory=_utcnow)
    proposer: Optional[str] = Field(
        default=None,
        description="Component or agent that created this proposal",
    )
    labels: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}
