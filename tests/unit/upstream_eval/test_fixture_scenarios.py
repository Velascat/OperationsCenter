from __future__ import annotations

import json
from pathlib import Path

from control_plane.upstream_eval import (
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


def _load_fixture(name: str) -> dict:
    path = Path("tests/fixtures/upstream_eval") / name
    return json.loads(path.read_text())


def _build_evidence(payload: dict) -> list[IntegrationFrictionEvidence]:
    return [
        IntegrationFrictionEvidence(
            upstream_target=record["upstream_target"],
            issue_key=record["issue_key"],
            category=PatchCandidateCategory(record["category"]),
            source_type=record["source_type"],
            summary=record["summary"],
            severity_hint=SeverityClass(record["severity_hint"]),
            architectural_impact_hint=ArchitecturalImpactClass(record["architectural_impact_hint"]),
            workaround_complexity_hint=WorkaroundComplexityClass(record["workaround_complexity_hint"]),
            workaround_reliability_hint=WorkaroundReliabilityClass(record["workaround_reliability_hint"]),
            divergence_risk_hint=DivergenceRiskClass(record["divergence_risk_hint"]),
            sample_size=record["sample_size"],
            occurrence_count=record["occurrence_count"],
        )
        for record in payload["evidence"]
    ]


def test_fixture_files_are_present() -> None:
    fixture_dir = Path("tests/fixtures/upstream_eval")
    assert sorted(fixture_dir.glob("*.json"))


def test_openclaw_strong_changed_file_friction_generates_proposal() -> None:
    payload = _load_fixture("openclaw_strong_changed_file_friction.json")
    report = UpstreamPatchEvaluator.default().analyze(_build_evidence(payload))
    assert report.friction_findings[0].frequency == FrequencyClass.PERSISTENT
    assert report.friction_findings[0].evidence_strength == EvidenceStrength.STRONG
    assert len(report.recommendations) == 1


def test_archon_weak_limitation_stays_adapter_first() -> None:
    payload = _load_fixture("archon_weak_support_limitation.json")
    report = UpstreamPatchEvaluator.default().analyze(_build_evidence(payload))
    assert report.friction_findings[0].evidence_strength == EvidenceStrength.WEAK
    assert report.recommendations == []


def test_kodo_moderate_ergonomic_issue_stays_in_adapter_layer() -> None:
    payload = _load_fixture("kodo_moderate_ergonomic_issue.json")
    report = UpstreamPatchEvaluator.default().analyze(_build_evidence(payload))
    assert report.friction_findings[0].frequency == FrequencyClass.RECURRING
    assert report.recommendations == []


def test_high_value_candidate_can_still_surface_high_divergence_risk() -> None:
    payload = _load_fixture("high_value_high_divergence_candidate.json")
    report = UpstreamPatchEvaluator.default().analyze(_build_evidence(payload))
    assert len(report.recommendations) == 1
    assert report.recommendations[0].divergence_risk == DivergenceRiskClass.HIGH
