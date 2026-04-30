# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from .analyze import UpstreamPatchEvaluator
from .models import (
    AdapterWorkaroundAssessment,
    ArchitecturalImpactClass,
    DivergenceRiskClass,
    EvidenceStrength,
    ExpectedValueClass,
    FrequencyClass,
    IntegrationFrictionEvidence,
    IntegrationFrictionFinding,
    MaintenanceBurdenClass,
    PatchCandidateCategory,
    SeverityClass,
    UpstreamPatchEvaluationReport,
    UpstreamPatchProposal,
    WorkaroundComplexityClass,
    WorkaroundReliabilityClass,
)

__all__ = [
    "UpstreamPatchEvaluator",
    "IntegrationFrictionEvidence",
    "IntegrationFrictionFinding",
    "AdapterWorkaroundAssessment",
    "UpstreamPatchProposal",
    "UpstreamPatchEvaluationReport",
    "PatchCandidateCategory",
    "FrequencyClass",
    "SeverityClass",
    "ArchitecturalImpactClass",
    "WorkaroundComplexityClass",
    "WorkaroundReliabilityClass",
    "EvidenceStrength",
    "MaintenanceBurdenClass",
    "DivergenceRiskClass",
    "ExpectedValueClass",
]
