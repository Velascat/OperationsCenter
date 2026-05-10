# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
upstream_eval/models.py — explicit models for evidence-based upstream patch evaluation.

This namespace exists to answer one narrow architectural question:
when does recurring adapter friction justify evaluating an upstream patch or
native integration improvement?

It does not own execution, routing, policy, or roadmap commitment.
It produces reviewable findings and proposals only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class PatchCandidateCategory(str, Enum):
    CAPABILITY_ENABLING = "capability_enabling"
    ERGONOMIC_SIMPLIFICATION = "ergonomic_simplification"
    RELIABILITY_IMPROVING = "reliability_improving"
    OBSERVABILITY_IMPROVING = "observability_improving"


class FrequencyClass(str, Enum):
    RARE = "rare"
    OCCASIONAL = "occasional"
    RECURRING = "recurring"
    PERSISTENT = "persistent"


class SeverityClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ArchitecturalImpactClass(str, Enum):
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"


class WorkaroundComplexityClass(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    HIGH = "high"


class WorkaroundReliabilityClass(str, Enum):
    STABLE = "stable"
    MIXED = "mixed"
    BRITTLE = "brittle"


class EvidenceStrength(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class MaintenanceBurdenClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DivergenceRiskClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExpectedValueClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IntegrationFrictionEvidence(BaseModel):
    """One normalized piece of recurring integration-friction evidence.

    This is intentionally abstracted away from backend-native raw output.
    It can be derived from support-check failures, retained execution records,
    changed-file evidence, operator pain, or retained tuning findings.
    """

    evidence_id: str = Field(default_factory=_new_id)
    upstream_target: str = Field(description="External system, e.g. 'openclaw', 'archon', 'kodo'")
    issue_key: str = Field(
        description="Stable clustering key for one recurring issue, e.g. 'changed_file_uncertainty'."
    )
    category: PatchCandidateCategory
    source_type: str = Field(
        description=(
            "Evidence origin, e.g. support_check_failure, execution_record, "
            "routing_finding, operator_pain, wrapper_complexity."
        )
    )
    summary: str
    severity_hint: SeverityClass = SeverityClass.MEDIUM
    architectural_impact_hint: ArchitecturalImpactClass = ArchitecturalImpactClass.MODERATE
    workaround_complexity_hint: WorkaroundComplexityClass = WorkaroundComplexityClass.MODERATE
    workaround_reliability_hint: WorkaroundReliabilityClass = WorkaroundReliabilityClass.MIXED
    maintenance_burden_hint: MaintenanceBurdenClass = MaintenanceBurdenClass.MEDIUM
    divergence_risk_hint: DivergenceRiskClass = DivergenceRiskClass.MEDIUM
    sample_size: int = Field(default=1, ge=1)
    occurrence_count: int = Field(default=1, ge=1)
    notes: str = ""

    model_config = {"frozen": True}


class IntegrationFrictionFinding(BaseModel):
    finding_id: str = Field(default_factory=_new_id)
    upstream_target: str
    category: PatchCandidateCategory
    summary: str
    frequency: FrequencyClass
    severity: SeverityClass
    architectural_impact: ArchitecturalImpactClass
    evidence_strength: EvidenceStrength
    occurrence_count: int = Field(ge=0)
    sample_size: int = Field(ge=0)
    issue_keys: list[str] = Field(default_factory=list)
    notes: str = ""

    model_config = {"frozen": True}


class AdapterWorkaroundAssessment(BaseModel):
    upstream_target: str
    issue_summary: str
    workaround_complexity: WorkaroundComplexityClass
    workaround_reliability: WorkaroundReliabilityClass
    ongoing_maintenance_cost: MaintenanceBurdenClass
    divergence_risk: DivergenceRiskClass
    prefer_adapter_first: bool = True
    notes: str = ""

    model_config = {"frozen": True}


class UpstreamPatchProposal(BaseModel):
    proposal_id: str = Field(default_factory=_new_id)
    upstream_target: str
    title: str
    summary: str
    candidate_class: PatchCandidateCategory
    justification: str
    expected_value: ExpectedValueClass
    risks: list[str] = Field(default_factory=list)
    maintenance_burden: MaintenanceBurdenClass
    divergence_risk: DivergenceRiskClass
    scope_notes: str = ""
    requires_review: bool = True
    source_finding_ids: list[str] = Field(default_factory=list)
    notes: str = ""

    model_config = {"frozen": True}

    @field_validator("requires_review")
    @classmethod
    def _requires_review_must_be_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "Phase 14 upstream patch proposals are review-only and cannot become active commitments."
            )
        return value


class UpstreamPatchEvaluationReport(BaseModel):
    report_id: str = Field(default_factory=_new_id)
    generated_at: datetime = Field(default_factory=_utcnow)
    targets_evaluated: list[str] = Field(default_factory=list)
    adapter_first_default: bool = True
    active_roadmap_reference: str = Field(
        default="tracked_work_items_only",
        description="Patch proposals are distinct from accepted roadmap items.",
    )
    proposal_status: str = Field(
        default="review_required",
        description="Generated patch candidates are recommendations only.",
    )
    friction_findings: list[IntegrationFrictionFinding] = Field(default_factory=list)
    workaround_assessments: list[AdapterWorkaroundAssessment] = Field(default_factory=list)
    recommendations: list[UpstreamPatchProposal] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    notes: str = ""

    model_config = {"frozen": True}
