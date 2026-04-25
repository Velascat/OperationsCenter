from __future__ import annotations

from operations_center.upstream_eval import (
    ArchitecturalImpactClass,
    DivergenceRiskClass,
    EvidenceStrength,
    FrequencyClass,
    IntegrationFrictionEvidence,
    PatchCandidateCategory,
    SeverityClass,
    UpstreamPatchEvaluator,
    WorkaroundComplexityClass,
    WorkaroundReliabilityClass,
)


def _evidence(
    *,
    upstream_target: str = "openclaw",
    issue_key: str = "changed_file_uncertainty",
    category: PatchCandidateCategory = PatchCandidateCategory.OBSERVABILITY_IMPROVING,
    source_type: str = "execution_record",
    summary: str = "Changed files remain unknown.",
    severity_hint: SeverityClass = SeverityClass.HIGH,
    architectural_impact_hint: ArchitecturalImpactClass = ArchitecturalImpactClass.MAJOR,
    workaround_complexity_hint: WorkaroundComplexityClass = WorkaroundComplexityClass.HIGH,
    workaround_reliability_hint: WorkaroundReliabilityClass = WorkaroundReliabilityClass.BRITTLE,
    divergence_risk_hint: DivergenceRiskClass = DivergenceRiskClass.MEDIUM,
    sample_size: int = 4,
    occurrence_count: int = 2,
) -> IntegrationFrictionEvidence:
    return IntegrationFrictionEvidence(
        upstream_target=upstream_target,
        issue_key=issue_key,
        category=category,
        source_type=source_type,
        summary=summary,
        severity_hint=severity_hint,
        architectural_impact_hint=architectural_impact_hint,
        workaround_complexity_hint=workaround_complexity_hint,
        workaround_reliability_hint=workaround_reliability_hint,
        divergence_risk_hint=divergence_risk_hint,
        sample_size=sample_size,
        occurrence_count=occurrence_count,
    )


def test_recurring_low_impact_friction_does_not_auto_recommend_patch() -> None:
    evaluator = UpstreamPatchEvaluator.default()
    evidence = [
        _evidence(
            upstream_target="kodo",
            issue_key="wrapper_flag_verbosity",
            category=PatchCandidateCategory.ERGONOMIC_SIMPLIFICATION,
            summary="Wrapper setup is mildly verbose.",
            severity_hint=SeverityClass.LOW,
            architectural_impact_hint=ArchitecturalImpactClass.MINOR,
            workaround_complexity_hint=WorkaroundComplexityClass.SIMPLE,
            workaround_reliability_hint=WorkaroundReliabilityClass.STABLE,
            sample_size=4,
            occurrence_count=4,
        )
    ]
    report = evaluator.analyze(evidence)
    assert report.friction_findings[0].frequency == FrequencyClass.RECURRING
    assert report.recommendations == []


def test_recurring_high_impact_strong_evidence_can_produce_patch_proposal() -> None:
    evaluator = UpstreamPatchEvaluator.default()
    evidence = [
        _evidence(sample_size=8, occurrence_count=4),
        _evidence(source_type="routing_finding", sample_size=8, occurrence_count=4),
    ]
    report = evaluator.analyze(evidence)
    assert report.friction_findings[0].evidence_strength == EvidenceStrength.STRONG
    assert len(report.recommendations) == 1
    proposal = report.recommendations[0]
    assert proposal.upstream_target == "openclaw"
    assert proposal.requires_review is True


def test_weak_evidence_leads_to_conservative_output() -> None:
    evaluator = UpstreamPatchEvaluator.default()
    report = evaluator.analyze([
        _evidence(upstream_target="archon", issue_key="provider_surface_gap", sample_size=1, occurrence_count=1)
    ])
    assert report.friction_findings[0].evidence_strength == EvidenceStrength.WEAK
    assert report.recommendations == []


def test_workaround_complexity_and_divergence_risk_are_reflected_in_proposal() -> None:
    evaluator = UpstreamPatchEvaluator.default()
    report = evaluator.analyze([
        _evidence(
            upstream_target="openclaw",
            divergence_risk_hint=DivergenceRiskClass.HIGH,
            sample_size=8,
            occurrence_count=4,
        ),
        _evidence(
            upstream_target="openclaw",
            source_type="operator_pain",
            divergence_risk_hint=DivergenceRiskClass.HIGH,
            sample_size=8,
            occurrence_count=4,
        ),
    ])
    proposal = report.recommendations[0]
    assert proposal.divergence_risk == DivergenceRiskClass.HIGH
    assert any("Divergence risk is high" in risk for risk in proposal.risks)


def test_active_roadmap_remains_distinct_from_generated_proposals() -> None:
    evaluator = UpstreamPatchEvaluator.default()
    report = evaluator.analyze([])
    assert report.active_roadmap_reference == "tracked_work_items_only"
    assert report.proposal_status == "review_required"


def test_classification_output_is_stable() -> None:
    evaluator = UpstreamPatchEvaluator.default()
    issue = _evidence(category=PatchCandidateCategory.RELIABILITY_IMPROVING)
    assert evaluator.classify(issue) == PatchCandidateCategory.RELIABILITY_IMPROVING
