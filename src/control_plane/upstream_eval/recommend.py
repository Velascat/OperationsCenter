"""
upstream_eval/recommend.py — conservative recommendation logic for upstream patch proposals.
"""

from __future__ import annotations

from .models import (
    AdapterWorkaroundAssessment,
    ArchitecturalImpactClass,
    DivergenceRiskClass,
    EvidenceStrength,
    ExpectedValueClass,
    FrequencyClass,
    IntegrationFrictionFinding,
    MaintenanceBurdenClass,
    PatchCandidateCategory,
    SeverityClass,
    UpstreamPatchProposal,
    WorkaroundComplexityClass,
    WorkaroundReliabilityClass,
)


def recommend_patch_proposals(
    findings: list[IntegrationFrictionFinding],
    assessments: list[AdapterWorkaroundAssessment],
) -> list[UpstreamPatchProposal]:
    by_target_summary = {
        (assessment.upstream_target, assessment.issue_summary): assessment
        for assessment in assessments
    }
    proposals: list[UpstreamPatchProposal] = []

    for finding in findings:
        assessment = _best_assessment_for_finding(finding, assessments, by_target_summary)
        if assessment is None:
            continue
        proposal = _proposal_for(finding, assessment)
        if proposal is not None:
            proposals.append(proposal)

    return proposals


def _best_assessment_for_finding(
    finding: IntegrationFrictionFinding,
    assessments: list[AdapterWorkaroundAssessment],
    by_target_summary: dict[tuple[str, str], AdapterWorkaroundAssessment],
) -> AdapterWorkaroundAssessment | None:
    direct = by_target_summary.get((finding.upstream_target, finding.summary))
    if direct is not None:
        return direct
    for assessment in assessments:
        if assessment.upstream_target == finding.upstream_target:
            return assessment
    return None


def _proposal_for(
    finding: IntegrationFrictionFinding,
    assessment: AdapterWorkaroundAssessment,
) -> UpstreamPatchProposal | None:
    if finding.evidence_strength == EvidenceStrength.WEAK:
        return None
    if finding.frequency not in (FrequencyClass.RECURRING, FrequencyClass.PERSISTENT):
        return None
    if finding.architectural_impact != ArchitecturalImpactClass.MAJOR:
        return None
    if assessment.workaround_complexity == WorkaroundComplexityClass.SIMPLE and assessment.workaround_reliability == WorkaroundReliabilityClass.STABLE:
        return None

    risks = [
        "Proposal remains review-only; this is not an accepted roadmap commitment.",
        f"Divergence risk is {assessment.divergence_risk.value}.",
        f"Ongoing maintenance burden is {assessment.ongoing_maintenance_cost.value}.",
    ]
    if assessment.divergence_risk == DivergenceRiskClass.HIGH:
        risks.append("High divergence risk may outweigh the value of carrying a fork unless the blocker is persistent.")

    return UpstreamPatchProposal(
        upstream_target=finding.upstream_target,
        title=_title_for(finding),
        summary=_summary_for(finding, assessment),
        candidate_class=finding.category,
        justification=finding.summary,
        expected_value=_expected_value(finding, assessment),
        risks=risks,
        maintenance_burden=assessment.ongoing_maintenance_cost,
        divergence_risk=assessment.divergence_risk,
        scope_notes=_scope_notes_for(finding, assessment),
        source_finding_ids=[finding.finding_id],
        notes="Adapter-first remains the baseline until a human explicitly accepts this proposal.",
    )


def _title_for(finding: IntegrationFrictionFinding) -> str:
    if finding.category == PatchCandidateCategory.OBSERVABILITY_IMPROVING:
        return f"Evaluate upstream observability patch for {finding.upstream_target}"
    if finding.category == PatchCandidateCategory.RELIABILITY_IMPROVING:
        return f"Evaluate upstream reliability patch for {finding.upstream_target}"
    if finding.category == PatchCandidateCategory.CAPABILITY_ENABLING:
        return f"Evaluate capability-enabling patch for {finding.upstream_target}"
    return f"Evaluate ergonomic simplification patch for {finding.upstream_target}"


def _summary_for(
    finding: IntegrationFrictionFinding,
    assessment: AdapterWorkaroundAssessment,
) -> str:
    return (
        f"{finding.upstream_target} has {finding.frequency.value} {finding.category.value.replace('_', ' ')} "
        f"friction with {finding.evidence_strength.value} evidence; current adapter workaround is "
        f"{assessment.workaround_complexity.value}/{assessment.workaround_reliability.value}."
    )


def _scope_notes_for(
    finding: IntegrationFrictionFinding,
    assessment: AdapterWorkaroundAssessment,
) -> str:
    return (
        "Evaluate a narrow upstream change that addresses the specific clustered issue. "
        "Do not redesign canonical contracts or reroute execution around the upstream repo."
    )


def _expected_value(
    finding: IntegrationFrictionFinding,
    assessment: AdapterWorkaroundAssessment,
) -> ExpectedValueClass:
    if (
        finding.severity in (SeverityClass.HIGH, SeverityClass.CRITICAL)
        and assessment.workaround_complexity == WorkaroundComplexityClass.HIGH
    ):
        return ExpectedValueClass.HIGH
    if assessment.ongoing_maintenance_cost == MaintenanceBurdenClass.HIGH:
        return ExpectedValueClass.HIGH
    return ExpectedValueClass.MEDIUM
