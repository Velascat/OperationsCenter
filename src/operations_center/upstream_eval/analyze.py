# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
upstream_eval/analyze.py — evidence-based evaluation of upstream patch candidates.
"""

from __future__ import annotations

from collections import defaultdict

from .models import (
    AdapterWorkaroundAssessment,
    ArchitecturalImpactClass,
    DivergenceRiskClass,
    EvidenceStrength,
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
from .recommend import recommend_patch_proposals


class UpstreamPatchEvaluator:
    """Evaluates recurring adapter friction without changing live integration behavior."""

    def analyze(
        self,
        evidence: list[IntegrationFrictionEvidence],
    ) -> UpstreamPatchEvaluationReport:
        findings, assessments = _analyze_evidence(evidence)
        recommendations = recommend_patch_proposals(findings, assessments)

        return UpstreamPatchEvaluationReport(
            targets_evaluated=sorted({item.upstream_target for item in evidence}),
            friction_findings=findings,
            workaround_assessments=assessments,
            recommendations=recommendations,
            limitations=_identify_limitations(evidence),
        )

    def classify(self, issue: IntegrationFrictionEvidence) -> PatchCandidateCategory:
        return issue.category

    def recommend(
        self,
        report: UpstreamPatchEvaluationReport,
    ) -> list[UpstreamPatchProposal]:
        if report.recommendations:
            return report.recommendations
        return recommend_patch_proposals(report.friction_findings, report.workaround_assessments)

    @classmethod
    def default(cls) -> "UpstreamPatchEvaluator":
        return cls()


def _analyze_evidence(
    evidence: list[IntegrationFrictionEvidence],
) -> tuple[list[IntegrationFrictionFinding], list[AdapterWorkaroundAssessment]]:
    if not evidence:
        return [], []

    grouped: dict[tuple[str, str, str], list[IntegrationFrictionEvidence]] = defaultdict(list)
    for item in evidence:
        grouped[(item.upstream_target, item.issue_key, item.category.value)].append(item)

    findings: list[IntegrationFrictionFinding] = []
    assessments: list[AdapterWorkaroundAssessment] = []

    for (upstream_target, issue_key, _category), items in sorted(grouped.items()):
        category = items[0].category
        total_occurrences = sum(item.occurrence_count for item in items)
        total_samples = sum(item.sample_size for item in items)
        finding = IntegrationFrictionFinding(
            upstream_target=upstream_target,
            category=category,
            summary=_build_summary(upstream_target, issue_key, items, total_occurrences),
            frequency=_frequency_class(total_occurrences),
            severity=_max_enum(item.severity_hint for item in items),
            architectural_impact=_max_enum(item.architectural_impact_hint for item in items),
            evidence_strength=_evidence_strength(total_occurrences, total_samples),
            occurrence_count=total_occurrences,
            sample_size=total_samples,
            issue_keys=[issue_key],
            notes=_build_finding_notes(items),
        )
        findings.append(finding)

        assessments.append(
            AdapterWorkaroundAssessment(
                upstream_target=upstream_target,
                issue_summary=finding.summary,
                workaround_complexity=_max_enum(item.workaround_complexity_hint for item in items),
                workaround_reliability=_max_enum(item.workaround_reliability_hint for item in items),
                ongoing_maintenance_cost=_max_enum(item.maintenance_burden_hint for item in items),
                divergence_risk=_max_enum(item.divergence_risk_hint for item in items),
                prefer_adapter_first=not _patch_maybe_justified(finding, items),
                notes=_build_assessment_notes(finding, items),
            )
        )

    return findings, assessments


def _identify_limitations(evidence: list[IntegrationFrictionEvidence]) -> list[str]:
    limitations: list[str] = []
    if not evidence:
        return ["No retained integration-friction evidence available; no upstream patch evaluation can be made."]

    if len(evidence) < 4:
        limitations.append(
            "Very few normalized friction evidence items are available; keep adapter-first by default."
        )

    weak_items = [item for item in evidence if item.sample_size <= 2 and item.occurrence_count <= 2]
    if weak_items:
        limitations.append(
            f"{len(weak_items)} evidence item(s) have very small samples; one-off incidents should not drive upstream proposals."
        )

    targets = {item.upstream_target for item in evidence}
    if len(targets) == 1:
        limitations.append(
            "Only one upstream target is represented in this evaluation window; cross-target prioritization is limited."
        )

    return limitations


def _build_summary(
    upstream_target: str,
    issue_key: str,
    items: list[IntegrationFrictionEvidence],
    total_occurrences: int,
) -> str:
    first = items[0]
    return (
        f"{upstream_target} shows {issue_key.replace('_', ' ')} friction across "
        f"{total_occurrences} retained occurrence(s); latest evidence: {first.summary}"
    )


def _build_finding_notes(items: list[IntegrationFrictionEvidence]) -> str:
    source_types = sorted({item.source_type for item in items})
    return f"Evidence sources: {', '.join(source_types)}"


def _build_assessment_notes(
    finding: IntegrationFrictionFinding,
    items: list[IntegrationFrictionEvidence],
) -> str:
    if finding.evidence_strength == EvidenceStrength.WEAK:
        return "Evidence remains weak; prefer documenting the limitation and continuing with the adapter."
    if any(item.divergence_risk_hint == DivergenceRiskClass.HIGH for item in items):
        return "Any upstream patch would carry high divergence risk and should be treated conservatively."
    return "Adapter-first remains the default unless recurring high-impact evidence continues."


def _patch_maybe_justified(
    finding: IntegrationFrictionFinding,
    items: list[IntegrationFrictionEvidence],
) -> bool:
    complexity = _max_enum(item.workaround_complexity_hint for item in items)
    reliability = _max_enum(item.workaround_reliability_hint for item in items)
    maintenance = _max_enum(item.maintenance_burden_hint for item in items)
    return (
        finding.evidence_strength in (EvidenceStrength.MODERATE, EvidenceStrength.STRONG)
        and finding.frequency in (FrequencyClass.RECURRING, FrequencyClass.PERSISTENT)
        and finding.architectural_impact == ArchitecturalImpactClass.MAJOR
        and (
            complexity == WorkaroundComplexityClass.HIGH
            or reliability == WorkaroundReliabilityClass.BRITTLE
            or maintenance == MaintenanceBurdenClass.HIGH
        )
    )


def _frequency_class(total_occurrences: int) -> FrequencyClass:
    if total_occurrences >= 8:
        return FrequencyClass.PERSISTENT
    if total_occurrences >= 4:
        return FrequencyClass.RECURRING
    if total_occurrences >= 2:
        return FrequencyClass.OCCASIONAL
    return FrequencyClass.RARE


def _evidence_strength(total_occurrences: int, total_samples: int) -> EvidenceStrength:
    if total_occurrences >= 8 and total_samples >= 16:
        return EvidenceStrength.STRONG
    if total_occurrences >= 4 and total_samples >= 8:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.WEAK


def _max_enum(values):
    values = list(values)
    enum_type = type(values[0])
    order_map = _ORDERING[enum_type]
    return max(values, key=lambda value: order_map[value])


_ORDERING = {
    SeverityClass: {
        SeverityClass.LOW: 0,
        SeverityClass.MEDIUM: 1,
        SeverityClass.HIGH: 2,
        SeverityClass.CRITICAL: 3,
    },
    ArchitecturalImpactClass: {
        ArchitecturalImpactClass.MINOR: 0,
        ArchitecturalImpactClass.MODERATE: 1,
        ArchitecturalImpactClass.MAJOR: 2,
    },
    WorkaroundComplexityClass: {
        WorkaroundComplexityClass.SIMPLE: 0,
        WorkaroundComplexityClass.MODERATE: 1,
        WorkaroundComplexityClass.HIGH: 2,
    },
    WorkaroundReliabilityClass: {
        WorkaroundReliabilityClass.STABLE: 0,
        WorkaroundReliabilityClass.MIXED: 1,
        WorkaroundReliabilityClass.BRITTLE: 2,
    },
    MaintenanceBurdenClass: {
        MaintenanceBurdenClass.LOW: 0,
        MaintenanceBurdenClass.MEDIUM: 1,
        MaintenanceBurdenClass.HIGH: 2,
    },
    DivergenceRiskClass: {
        DivergenceRiskClass.LOW: 0,
        DivergenceRiskClass.MEDIUM: 1,
        DivergenceRiskClass.HIGH: 2,
    },
}
