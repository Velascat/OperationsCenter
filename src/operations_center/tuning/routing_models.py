# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
tuning/routing_models.py — Models for evidence-driven routing and backend strategy tuning.

These models are the outputs of the strategy tuning layer. They are:
  - derived from retained ExecutionRecord evidence
  - separate from active routing policy
  - intended for human review before any config change
  - frozen Pydantic (immutable output types)

Separation rule:
  BackendComparisonSummary   → what the evidence shows about a backend
  StrategyFinding            → a bounded observation from that evidence
  RoutingTuningProposal      → a candidate change, NOT an active policy update
  StrategyAnalysisReport     → the full analysis package
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Classification enums
# ---------------------------------------------------------------------------


class EvidenceStrength(str, Enum):
    """How much evidence backs a finding or recommendation.

    WEAK     — fewer than 8 samples or contradictory signals
    MODERATE — 8–19 samples with consistent signals
    STRONG   — 20+ samples with consistent signals
    """
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class LatencyClass(str, Enum):
    """Approximate execution latency class derived from retained duration data."""
    FAST = "fast"       # median < 30 s
    MEDIUM = "medium"   # median 30–120 s
    SLOW = "slow"       # median > 120 s
    UNKNOWN = "unknown" # duration data unavailable


class ReliabilityClass(str, Enum):
    """Success-rate reliability class for a backend/lane combination."""
    LOW = "low"       # success_rate < 0.60
    MEDIUM = "medium" # success_rate 0.60–0.84
    HIGH = "high"     # success_rate >= 0.85


class ChangeEvidenceClass(str, Enum):
    """Quality of changed-file evidence produced by a backend/lane combination.

    Derived from the ChangedFilesEvidence.status distribution across runs:
      strong  — >= 80% of runs have KNOWN or NONE status
      partial — 40–79% have KNOWN or NONE
      poor    — < 40% have KNOWN or NONE
    """
    STRONG = "strong"
    PARTIAL = "partial"
    POOR = "poor"
    UNKNOWN = "unknown"  # no runs or all NOT_APPLICABLE


# ---------------------------------------------------------------------------
# BackendComparisonSummary
# ---------------------------------------------------------------------------


class BackendComparisonSummary(BaseModel):
    """Normalized comparison of one backend/lane combination over retained records.

    This is evidence-derived, not a policy statement.
    """

    backend: str = Field(description="Backend name, e.g. 'kodo', 'archon', 'openclaw'")
    lane: str = Field(description="Lane name, e.g. 'claude_cli', 'aider_local'")
    task_type_scope: list[str] = Field(
        default_factory=list,
        description="Task types covered. Empty list means all task types in the sample.",
    )
    risk_scope: list[str] = Field(
        default_factory=list,
        description="Risk levels covered. Empty list means all risk levels in the sample.",
    )
    sample_size: int = Field(description="Number of ExecutionRecords analyzed")
    evidence_strength: EvidenceStrength

    # Rates (0.0–1.0)
    success_rate: float = Field(ge=0.0, le=1.0)
    failure_rate: float = Field(ge=0.0, le=1.0)
    partial_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Rate of runs that completed without changes (NO_CHANGES outcome)",
    )
    timeout_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    validation_pass_rate: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Rate of runs where validation passed (0 if validation always skipped)",
    )
    validation_skip_rate: float = Field(
        ge=0.0, le=1.0, default=0.0,
        description="Rate of runs where validation was skipped",
    )

    # Quality classes
    latency_class: LatencyClass = LatencyClass.UNKNOWN
    reliability_class: ReliabilityClass
    change_evidence_class: ChangeEvidenceClass

    # Optional aggregate latency (ms)
    median_duration_ms: Optional[int] = Field(
        default=None,
        description="Median execution duration in ms, if duration data available in metadata",
    )

    notes: str = ""

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# StrategyFinding
# ---------------------------------------------------------------------------


class StrategyFinding(BaseModel):
    """A bounded observation about routing-relevant patterns in retained evidence.

    Findings are not recommendations. They are observations that may lead to
    candidate recommendations. Every finding must carry its evidence strength.
    """

    finding_id: str = Field(default_factory=_new_id)
    category: str = Field(
        description=(
            "Category: 'reliability', 'change_evidence', 'validation', 'latency', "
            "'coverage', 'sparse_data', 'contradictory'"
        )
    )
    summary: str = Field(description="One-sentence observation.")
    evidence_strength: EvidenceStrength
    affected_lanes: list[str] = Field(default_factory=list)
    affected_backends: list[str] = Field(default_factory=list)
    task_scope: list[str] = Field(
        default_factory=list,
        description="Task types this finding applies to. Empty = general.",
    )
    supporting_data: dict[str, object] = Field(
        default_factory=dict,
        description="Key metrics that support this finding.",
    )
    notes: str = ""

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# RoutingTuningProposal
# ---------------------------------------------------------------------------


class RoutingTuningProposal(BaseModel):
    """A candidate routing strategy change, derived from evidence.

    This is NOT an active policy change. It is a reviewable proposal
    that requires human approval before any config update in Phase 13.

    The separation between proposal and active policy is mandatory.
    """

    proposal_id: str = Field(default_factory=_new_id)
    summary: str = Field(description="One-sentence description of the proposed change.")
    proposed_change: str = Field(description="Concrete description of what would change.")
    justification: str = Field(description="Why this change is suggested, tied to evidence.")
    evidence_strength: EvidenceStrength
    affected_policy_area: str = Field(
        description=(
            "Which policy area this touches: 'lane_selection', 'backend_preference', "
            "'validation_requirements', 'escalation_threshold', 'local_first_threshold'"
        )
    )
    risk_notes: str = Field(
        default="",
        description="Any risk, caveat, or counter-evidence to weigh before accepting.",
    )
    requires_review: bool = Field(
        default=True,
        description="Always True in Phase 13 — no auto-application of routing strategy changes.",
    )
    source_finding_ids: list[str] = Field(
        default_factory=list,
        description="finding_id values from StrategyFindings that drove this proposal.",
    )
    policy_guardrails: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit reminders that this proposal cannot override active policy, "
            "safety guardrails, or review gates."
        ),
    )
    notes: str = ""

    model_config = {"frozen": True}

    @field_validator("requires_review")
    @classmethod
    def _requires_review_must_remain_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "Phase 13 routing tuning proposals are review-only and cannot auto-approve changes."
            )
        return value


# ---------------------------------------------------------------------------
# StrategyAnalysisReport
# ---------------------------------------------------------------------------


class StrategyAnalysisReport(BaseModel):
    """A full routing strategy analysis from retained execution evidence.

    Contains:
      - explicit references to current policy vs historical evidence vs proposals
      - comparison summaries per backend/lane combination
      - findings derived from those summaries
      - candidate recommendations (require human review)
      - limitations (sparse data, unavailable dimensions, etc.)

    This is the primary output of StrategyTuningService.analyze().
    It does not modify active routing policy. It informs a human reviewer
    who decides whether to accept, reject, or defer each recommendation.
    """

    report_id: str = Field(default_factory=_new_id)
    generated_at: datetime = Field(default_factory=_utcnow)
    record_count: int = Field(description="Number of ExecutionRecords analyzed")
    active_policy_reference: str = Field(
        default="switchboard_current_policy",
        description="Where the currently active routing policy lives. Not mutated here.",
    )
    observed_evidence_source: str = Field(
        default="retained_execution_records",
        description="What historical evidence this report was derived from.",
    )
    proposed_changes_status: str = Field(
        default="review_required",
        description="All routing strategy changes remain proposals in Phase 13.",
    )
    policy_guardrails_applied: list[str] = Field(
        default_factory=list,
        description="Explicit policy bounds that tuning recommendations cannot override.",
    )

    comparison_summaries: list[BackendComparisonSummary] = Field(default_factory=list)
    findings: list[StrategyFinding] = Field(default_factory=list)
    recommendations: list[RoutingTuningProposal] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=list,
        description="Honest statements about what this analysis cannot reliably determine.",
    )
    notes: str = ""

    model_config = {"frozen": True}
