"""
routing.py — LaneDecision: the canonical routing decision emitted by SwitchBoard.

A LaneDecision is SwitchBoard's answer to a TaskProposal. It names the selected
lane and backend, carries the rationale, and stamps the decision time. It does
not modify the proposal — it references it by ID.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .enums import BackendName, LaneName


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class LaneDecision(BaseModel):
    """
    SwitchBoard's routing decision for a TaskProposal.

    Consumed by OperationsCenter's execution boundary to know which backend to
    invoke and with which configuration. SwitchBoard does not embed execution
    logic; this is pure routing metadata.
    """

    # Identity
    decision_id: str = Field(default_factory=_new_id)
    proposal_id: str = Field(description="ID of the TaskProposal this decision routes")

    # The decision
    selected_lane: LaneName = Field(description="Execution lane chosen for this task")
    selected_backend: BackendName = Field(description="Backend within the lane")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Policy confidence in this routing choice (0.0–1.0)",
    )

    # Rationale (for logging and audit)
    policy_rule_matched: Optional[str] = Field(
        default=None,
        description="Name of the policy rule that drove this decision",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Human-readable explanation of the routing choice",
    )
    alternatives_considered: list[LaneName] = Field(
        default_factory=list,
        description="Other lanes that were evaluated but not selected",
    )

    # Metadata
    decided_at: datetime = Field(default_factory=_utcnow)
    switchboard_version: Optional[str] = Field(default=None)

    model_config = {"frozen": True}
