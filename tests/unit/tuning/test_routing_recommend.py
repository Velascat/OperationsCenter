"""
Unit tests for tuning/routing_recommend.py.

Covers:
  TestDeriveFindings      — findings from comparison summaries
  TestGenerateRecommendations — proposals from findings
  TestWeakEvidenceBoundary   — WEAK evidence stays in sparse_data only
  TestRecommendationPolicy   — proposals always require review
"""

from __future__ import annotations

import pytest

from control_plane.tuning.routing_models import (
    BackendComparisonSummary,
    ChangeEvidenceClass,
    EvidenceStrength,
    LatencyClass,
    ReliabilityClass,
    RoutingTuningProposal,
    StrategyFinding,
)
from control_plane.tuning.routing_recommend import derive_findings, generate_recommendations

from .conftest import (
    make_failure,
    make_n_failures,
    make_n_successes,
    make_success,
    make_timeout,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summary(
    backend: str = "kodo",
    lane: str = "claude_cli",
    sample_size: int = 10,
    evidence_strength: EvidenceStrength = EvidenceStrength.MODERATE,
    success_rate: float = 0.9,
    failure_rate: float = 0.1,
    reliability_class: ReliabilityClass = ReliabilityClass.HIGH,
    change_evidence_class: ChangeEvidenceClass = ChangeEvidenceClass.STRONG,
    latency_class: LatencyClass = LatencyClass.MEDIUM,
    timeout_rate: float = 0.0,
    validation_skip_rate: float = 0.0,
    validation_pass_rate: float = 1.0,
    **kw,
) -> BackendComparisonSummary:
    return BackendComparisonSummary(
        backend=backend,
        lane=lane,
        sample_size=sample_size,
        evidence_strength=evidence_strength,
        success_rate=success_rate,
        failure_rate=failure_rate,
        partial_rate=0.0,
        timeout_rate=timeout_rate,
        validation_pass_rate=validation_pass_rate,
        validation_skip_rate=validation_skip_rate,
        reliability_class=reliability_class,
        change_evidence_class=change_evidence_class,
        latency_class=latency_class,
        **kw,
    )


# ---------------------------------------------------------------------------
# TestDeriveFindings
# ---------------------------------------------------------------------------


class TestDeriveFindings:
    def test_high_reliability_produces_finding(self):
        s = _summary(reliability_class=ReliabilityClass.HIGH, success_rate=0.9)
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "reliability" in cats
        summaries = [f.summary for f in findings if f.category == "reliability"]
        assert any("high reliability" in sm for sm in summaries)

    def test_low_reliability_produces_finding(self):
        s = _summary(
            reliability_class=ReliabilityClass.LOW,
            success_rate=0.5,
            failure_rate=0.5,
        )
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "reliability" in cats
        summaries = [f.summary for f in findings if f.category == "reliability"]
        assert any("low reliability" in sm for sm in summaries)

    def test_poor_change_evidence_produces_finding(self):
        s = _summary(change_evidence_class=ChangeEvidenceClass.POOR)
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "change_evidence" in cats

    def test_partial_change_evidence_produces_finding(self):
        s = _summary(change_evidence_class=ChangeEvidenceClass.PARTIAL)
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "change_evidence" in cats

    def test_high_timeout_rate_produces_finding(self):
        s = _summary(timeout_rate=0.25)
        findings = derive_findings([s])
        timeout_findings = [f for f in findings if "timeout" in f.summary.lower()]
        assert timeout_findings

    def test_high_validation_skip_rate_produces_finding(self):
        s = _summary(validation_skip_rate=0.9, validation_pass_rate=0.0)
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "validation" in cats

    def test_slow_latency_produces_finding(self):
        s = _summary(latency_class=LatencyClass.SLOW, median_duration_ms=250_000)
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "latency" in cats

    def test_contradictory_high_reliability_poor_change_evidence(self):
        s = _summary(
            reliability_class=ReliabilityClass.HIGH,
            success_rate=0.9,
            change_evidence_class=ChangeEvidenceClass.POOR,
        )
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "contradictory" in cats

    def test_findings_carry_affected_lane_and_backend(self):
        s = _summary(backend="openclaw", lane="claude_cli")
        findings = derive_findings([s])
        for f in findings:
            assert "openclaw" in f.affected_backends
            assert "claude_cli" in f.affected_lanes

    def test_clean_summary_produces_only_reliability_finding(self):
        s = _summary(
            reliability_class=ReliabilityClass.HIGH,
            success_rate=0.9,
            failure_rate=0.1,
            change_evidence_class=ChangeEvidenceClass.STRONG,
            latency_class=LatencyClass.FAST,
            timeout_rate=0.0,
            validation_skip_rate=0.0,
        )
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "reliability" in cats
        assert "change_evidence" not in cats
        assert "validation" not in cats
        assert "latency" not in cats
        assert "contradictory" not in cats

    def test_multiple_summaries_multiple_finding_sets(self):
        s1 = _summary(backend="kodo", lane="claude_cli")
        s2 = _summary(
            backend="archon",
            lane="claude_cli",
            reliability_class=ReliabilityClass.LOW,
            success_rate=0.5,
            failure_rate=0.5,
        )
        findings = derive_findings([s1, s2])
        backends = {b for f in findings for b in f.affected_backends}
        assert "kodo" in backends
        assert "archon" in backends

    def test_empty_summaries_returns_empty(self):
        assert derive_findings([]) == []


# ---------------------------------------------------------------------------
# TestWeakEvidenceBoundary
# ---------------------------------------------------------------------------


class TestWeakEvidenceBoundary:
    def test_weak_evidence_produces_sparse_data_finding(self):
        s = _summary(
            sample_size=3,
            evidence_strength=EvidenceStrength.WEAK,
        )
        findings = derive_findings([s])
        assert len(findings) == 1
        assert findings[0].category == "sparse_data"

    def test_weak_evidence_does_not_produce_reliability_finding(self):
        s = _summary(
            sample_size=1,
            evidence_strength=EvidenceStrength.WEAK,
            reliability_class=ReliabilityClass.LOW,
            success_rate=0.0,
        )
        findings = derive_findings([s])
        cats = [f.category for f in findings]
        assert "reliability" not in cats

    def test_weak_findings_produce_no_recommendations(self):
        weak_finding = StrategyFinding(
            category="sparse_data",
            summary="Only 3 samples.",
            evidence_strength=EvidenceStrength.WEAK,
        )
        proposals = generate_recommendations([weak_finding])
        assert proposals == []

    def test_moderate_reliability_finding_produces_recommendation(self):
        s = _summary(
            evidence_strength=EvidenceStrength.MODERATE,
            reliability_class=ReliabilityClass.HIGH,
        )
        findings = derive_findings([s])
        moderate_findings = [f for f in findings if f.evidence_strength != EvidenceStrength.WEAK]
        proposals = generate_recommendations(moderate_findings)
        assert proposals  # at least one recommendation


# ---------------------------------------------------------------------------
# TestGenerateRecommendations
# ---------------------------------------------------------------------------


class TestGenerateRecommendations:
    def _finding(self, category: str, summary: str, strength: EvidenceStrength, **kw) -> StrategyFinding:
        return StrategyFinding(
            category=category,
            summary=summary,
            evidence_strength=strength,
            affected_backends=kw.get("affected_backends", ["kodo"]),
            affected_lanes=kw.get("affected_lanes", ["claude_cli"]),
        )

    def test_low_reliability_finding_produces_proposal(self):
        f = self._finding(
            "reliability",
            "kodo @ claude_cli shows low reliability: 50% success rate across 10 runs.",
            EvidenceStrength.MODERATE,
        )
        proposals = generate_recommendations([f])
        assert len(proposals) == 1
        assert "backend_preference" == proposals[0].affected_policy_area
        assert proposals[0].requires_review is True

    def test_high_reliability_finding_produces_proposal(self):
        f = self._finding(
            "reliability",
            "kodo @ claude_cli shows high reliability: 90% success rate across 10 runs.",
            EvidenceStrength.MODERATE,
        )
        proposals = generate_recommendations([f])
        assert len(proposals) == 1

    def test_change_evidence_poor_produces_proposal(self):
        f = self._finding(
            "change_evidence",
            "openclaw @ claude_cli produces poor changed-file evidence.",
            EvidenceStrength.STRONG,
        )
        proposals = generate_recommendations([f])
        assert len(proposals) == 1
        assert "backend_preference" == proposals[0].affected_policy_area

    def test_policy_guardrails_are_attached_to_generated_proposals(self):
        f = self._finding(
            "reliability",
            "kodo @ claude_cli shows high reliability: 90% success rate across 10 runs.",
            EvidenceStrength.MODERATE,
        )
        proposals = generate_recommendations(
            [f],
            policy_guardrails=["Cannot override explicit review gates."],
        )
        assert proposals[0].policy_guardrails == ["Cannot override explicit review gates."]

    def test_validation_gap_produces_proposal(self):
        f = self._finding(
            "validation",
            "kodo @ aider_local skips validation in 85% of runs.",
            EvidenceStrength.MODERATE,
        )
        proposals = generate_recommendations([f])
        assert len(proposals) == 1
        assert "validation_requirements" == proposals[0].affected_policy_area

    def test_latency_slow_produces_proposal(self):
        f = self._finding(
            "latency",
            "archon @ claude_cli is slow (median 300000 ms) across 20 runs.",
            EvidenceStrength.STRONG,
        )
        proposals = generate_recommendations([f])
        assert len(proposals) == 1
        assert "local_first_threshold" == proposals[0].affected_policy_area

    def test_contradictory_produces_proposal(self):
        f = self._finding(
            "contradictory",
            "kodo @ claude_cli is reliable by success rate but produces poor changed-file evidence.",
            EvidenceStrength.STRONG,
        )
        proposals = generate_recommendations([f])
        assert len(proposals) == 1
        assert "backend_preference" == proposals[0].affected_policy_area

    def test_all_proposals_require_review(self):
        findings = [
            self._finding("reliability", "kodo @ x shows low reliability: 50% success rate across 10 runs.", EvidenceStrength.MODERATE),
            self._finding("change_evidence", "kodo @ x produces poor changed-file evidence.", EvidenceStrength.STRONG),
        ]
        proposals = generate_recommendations(findings)
        assert all(p.requires_review is True for p in proposals)

    def test_proposals_carry_source_finding_ids(self):
        f = self._finding(
            "reliability",
            "kodo @ claude_cli shows low reliability: 50% success rate across 10 runs.",
            EvidenceStrength.MODERATE,
        )
        proposals = generate_recommendations([f])
        assert f.finding_id in proposals[0].source_finding_ids

    def test_recommendations_are_distinct_from_active_policy(self):
        f = self._finding("reliability", "kodo @ x shows high reliability: 90% success rate across 10 runs.", EvidenceStrength.STRONG)
        proposals = generate_recommendations([f])
        # Proposals are frozen Pydantic objects — they cannot mutate active policy
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            proposals[0].requires_review = False

    def test_empty_findings_returns_empty(self):
        assert generate_recommendations([]) == []
