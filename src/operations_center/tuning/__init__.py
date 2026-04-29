# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
tuning/ — bounded self-tuning and evidence-driven strategy analysis.

Two sub-systems live here:

1. Proposal-creation tuning (existing, Phase X):
   TuningRegulatorService, FamilyMetrics, TuningRecommendation, etc.
   Analyzes decision/proposer artifact directories to tune suppression thresholds.

2. Routing strategy tuning (Phase 13):
   StrategyTuningService — analyzes retained ExecutionRecords to produce
   routing/backend strategy comparisons, findings, and reviewable proposals.

Public API for routing strategy tuning:
    StrategyTuningService   — analyze(records) → StrategyAnalysisReport
    StrategyAnalysisReport  — full analysis output
    BackendComparisonSummary — one backend/lane compared over retained evidence
    StrategyFinding         — bounded observation from evidence
    RoutingTuningProposal   — candidate change (always requires human review)
    EvidenceStrength        — WEAK / MODERATE / STRONG
    ReliabilityClass        — LOW / MEDIUM / HIGH
    ChangeEvidenceClass     — POOR / PARTIAL / STRONG / UNKNOWN
    LatencyClass            — FAST / MEDIUM / SLOW / UNKNOWN
    compare_backends        — lower-level comparison function
    derive_findings         — lower-level finding derivation
    generate_recommendations — lower-level recommendation derivation
"""

from .analyze import StrategyTuningService
from .compare import compare_backends, compare_by_task_type
from .routing_models import (
    BackendComparisonSummary,
    ChangeEvidenceClass,
    EvidenceStrength,
    LatencyClass,
    ReliabilityClass,
    RoutingTuningProposal,
    StrategyAnalysisReport,
    StrategyFinding,
)
from .routing_recommend import derive_findings, generate_recommendations

__all__ = [
    # Routing strategy tuning (Phase 13)
    "StrategyTuningService",
    "StrategyAnalysisReport",
    "BackendComparisonSummary",
    "StrategyFinding",
    "RoutingTuningProposal",
    "EvidenceStrength",
    "ReliabilityClass",
    "ChangeEvidenceClass",
    "LatencyClass",
    "compare_backends",
    "compare_by_task_type",
    "derive_findings",
    "generate_recommendations",
]
