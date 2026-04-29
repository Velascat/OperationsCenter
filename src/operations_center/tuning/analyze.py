# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
tuning/analyze.py — StrategyTuningService: the primary entry point for Phase 13 tuning.

Usage:

    service = StrategyTuningService()
    report = service.analyze(records)

    # report.comparison_summaries — one per (lane, backend) pair
    # report.findings             — bounded observations from evidence
    # report.recommendations      — candidate changes (all require human review)
    # report.limitations          — what this analysis can't reliably determine

The service never modifies active routing policy. It produces a
StrategyAnalysisReport that informs a human reviewer.

Architecture rule:
    current active policy  ≠  observed historical evidence  ≠  proposed tuning changes
    All three are kept separate. This module only produces the third.
"""

from __future__ import annotations

from operations_center.observability.models import ExecutionRecord

from .compare import compare_backends
from .routing_models import StrategyAnalysisReport
from .routing_recommend import derive_findings, generate_recommendations


# Minimum records before any useful analysis is possible.
_MIN_RECORDS_FOR_ANALYSIS = 1

DEFAULT_POLICY_GUARDRAILS = [
    "Routing tuning cannot override explicit repo policy, safety guardrails, or blocked task/path/tool rules.",
    "Routing tuning cannot mutate active SwitchBoard policy; it only emits reviewable proposals.",
    "Any accepted routing change must flow through a separate reviewed config or policy update.",
]


class StrategyTuningService:
    """Analyzes retained execution evidence for routing and backend strategy.

    Backed by compare.py (summarize) and routing_recommend.py (find/propose).

    Default instantiation:
        service = StrategyTuningService()

    Inject dependencies for testing or alternative strategies:
        service = StrategyTuningService(
            compare_fn=my_compare,
            findings_fn=my_findings,
            recommendations_fn=my_recommendations,
        )
    """

    def __init__(
        self,
        compare_fn=None,
        findings_fn=None,
        recommendations_fn=None,
    ) -> None:
        self._compare = compare_fn or compare_backends
        self._findings = findings_fn or derive_findings
        self._recommendations = recommendations_fn or generate_recommendations

    def analyze(
        self,
        records: list[ExecutionRecord],
        *,
        task_type_scope: list[str] | None = None,
        risk_scope: list[str] | None = None,
        policy_guardrails: list[str] | None = None,
    ) -> StrategyAnalysisReport:
        """Analyze retained execution evidence and produce a StrategyAnalysisReport.

        Args:
            records:         ExecutionRecords to analyze. Typically all retained
                             records, or a filtered window.
            task_type_scope: Optional filter by task type (metadata["task_type"]).
            risk_scope:      Optional filter by risk level (metadata["risk_level"]).

        Returns:
            A frozen StrategyAnalysisReport with comparisons, findings,
            recommendations, and honest limitations.
        """
        guardrails = list(policy_guardrails or DEFAULT_POLICY_GUARDRAILS)
        limitations = _identify_limitations(records)

        comparisons = self._compare(
            records,
            task_type_scope=task_type_scope,
            risk_scope=risk_scope,
        )
        findings = self._findings(comparisons, records)
        recommendations = self._recommendations(
            findings,
            policy_guardrails=guardrails,
        )
        limitations.extend(_limitations_from_findings(findings))

        return StrategyAnalysisReport(
            record_count=len(records),
            policy_guardrails_applied=guardrails,
            comparison_summaries=comparisons,
            findings=findings,
            recommendations=recommendations,
            limitations=limitations,
        )

    @classmethod
    def default(cls) -> "StrategyTuningService":
        """Create with default production dependencies."""
        return cls()

    def compare(
        self,
        records: list[ExecutionRecord],
        *,
        task_type_scope: list[str] | None = None,
        risk_scope: list[str] | None = None,
    ):
        """Expose comparison as an inspectable first-class step."""
        return self._compare(
            records,
            task_type_scope=task_type_scope,
            risk_scope=risk_scope,
        )

    def recommend(
        self,
        report: StrategyAnalysisReport,
        *,
        policy_guardrails: list[str] | None = None,
    ):
        """Derive proposals from an existing report without rerunning comparison."""
        if report.recommendations and not policy_guardrails:
            return report.recommendations
        return self._recommendations(
            report.findings,
            policy_guardrails=list(policy_guardrails or report.policy_guardrails_applied),
        )


# ---------------------------------------------------------------------------
# Limitation identification
# ---------------------------------------------------------------------------


def _identify_limitations(records: list[ExecutionRecord]) -> list[str]:
    """Produce honest statements about what this analysis cannot determine."""
    limitations: list[str] = []

    if not records:
        limitations.append("No execution records available; analysis is empty.")
        return limitations

    if len(records) < 8:
        limitations.append(
            f"Only {len(records)} record(s) available; most findings will have weak evidence. "
            "Accumulate more runs before trusting recommendations."
        )

    # Check for missing duration data (latency class)
    records_with_duration = sum(1 for r in records if r.metadata.get("duration_ms") is not None)
    if records_with_duration == 0:
        limitations.append(
            "No execution duration data found in record metadata. "
            "Latency class is UNKNOWN for all comparisons. "
            "Add 'duration_ms' to ExecutionRecord.metadata to enable latency analysis."
        )
    elif records_with_duration < len(records):
        pct = round(100 * records_with_duration / len(records))
        limitations.append(
            f"Duration metadata available for only {pct}% of records ({records_with_duration}/{len(records)}). "
            "Latency class may not be representative."
        )

    # Check for validation coverage
    records_with_validation = sum(
        1 for r in records
        if r.validation_evidence.status.value != "skipped"
    )
    if records_with_validation == 0:
        limitations.append(
            "All records have skipped validation. "
            "Validation quality cannot be assessed; "
            "validation_pass_rate is 0 for all comparisons."
        )

    # Check for missing lane/backend metadata
    records_missing_lane = sum(1 for r in records if not r.lane)
    records_missing_backend = sum(1 for r in records if not r.backend)
    if records_missing_lane > 0 or records_missing_backend > 0:
        limitations.append(
            f"{records_missing_lane} record(s) are missing lane and/or "
            f"{records_missing_backend} are missing backend metadata — "
            "these appear under 'unknown' in comparisons."
        )

    # Check for task_type metadata coverage
    records_with_task_type = sum(1 for r in records if r.metadata.get("task_type"))
    if records_with_task_type == 0 and len(records) > 0:
        limitations.append(
            "No records carry task_type metadata. "
            "Per-task-type comparison is unavailable."
        )

    return limitations


def _limitations_from_findings(findings) -> list[str]:
    limitations: list[str] = []
    contradictory = [f for f in findings if f.category == "contradictory"]
    if contradictory:
        limitations.append(
            "Some findings are contradictory across dimensions; review changed-file evidence, "
            "validation coverage, and task scope before adjusting routing defaults."
        )
    return limitations
