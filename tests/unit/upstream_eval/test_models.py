from __future__ import annotations

import pytest
from pydantic import ValidationError

from control_plane.upstream_eval.models import (
    EvidenceStrength,
    FrequencyClass,
    IntegrationFrictionEvidence,
    PatchCandidateCategory,
    SeverityClass,
    UpstreamPatchEvaluationReport,
    UpstreamPatchProposal,
)


def test_evidence_model_construction() -> None:
    evidence = IntegrationFrictionEvidence(
        upstream_target="openclaw",
        issue_key="changed_file_uncertainty",
        category=PatchCandidateCategory.OBSERVABILITY_IMPROVING,
        source_type="execution_record",
        summary="Changed files are frequently unknown.",
    )
    assert evidence.upstream_target == "openclaw"
    assert evidence.occurrence_count == 1


def test_proposal_requires_review_is_enforced() -> None:
    with pytest.raises(ValidationError):
        UpstreamPatchProposal(
            upstream_target="kodo",
            title="x",
            summary="y",
            candidate_class=PatchCandidateCategory.ERGONOMIC_SIMPLIFICATION,
            justification="z",
            expected_value="medium",
            maintenance_burden="medium",
            divergence_risk="low",
            requires_review=False,
        )


def test_report_defaults_keep_roadmap_separate() -> None:
    report = UpstreamPatchEvaluationReport()
    assert report.adapter_first_default is True
    assert report.active_roadmap_reference == "tracked_work_items_only"
    assert report.proposal_status == "review_required"


def test_enum_values_are_stable() -> None:
    assert EvidenceStrength.STRONG == "strong"
    assert FrequencyClass.PERSISTENT == "persistent"
    assert SeverityClass.CRITICAL == "critical"
