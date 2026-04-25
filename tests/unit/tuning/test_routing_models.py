"""
Unit tests for tuning/routing_models.py.

Covers: model construction, frozen constraints, enum values, defaults.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from operations_center.tuning.routing_models import (
    BackendComparisonSummary,
    ChangeEvidenceClass,
    EvidenceStrength,
    LatencyClass,
    ReliabilityClass,
    RoutingTuningProposal,
    StrategyAnalysisReport,
    StrategyFinding,
)


class TestEvidenceStrength:
    def test_three_levels(self):
        assert EvidenceStrength.WEAK == "weak"
        assert EvidenceStrength.MODERATE == "moderate"
        assert EvidenceStrength.STRONG == "strong"


class TestLatencyClass:
    def test_four_values(self):
        assert set(LatencyClass) == {
            LatencyClass.FAST, LatencyClass.MEDIUM,
            LatencyClass.SLOW, LatencyClass.UNKNOWN,
        }


class TestReliabilityClass:
    def test_three_values(self):
        assert set(ReliabilityClass) == {
            ReliabilityClass.LOW, ReliabilityClass.MEDIUM, ReliabilityClass.HIGH,
        }


class TestChangeEvidenceClass:
    def test_four_values(self):
        assert set(ChangeEvidenceClass) == {
            ChangeEvidenceClass.POOR, ChangeEvidenceClass.PARTIAL,
            ChangeEvidenceClass.STRONG, ChangeEvidenceClass.UNKNOWN,
        }


class TestBackendComparisonSummary:
    def _make(self, **kw) -> BackendComparisonSummary:
        defaults = dict(
            backend="kodo",
            lane="claude_cli",
            sample_size=10,
            evidence_strength=EvidenceStrength.MODERATE,
            success_rate=0.9,
            failure_rate=0.1,
            partial_rate=0.0,
            reliability_class=ReliabilityClass.HIGH,
            change_evidence_class=ChangeEvidenceClass.STRONG,
        )
        defaults.update(kw)
        return BackendComparisonSummary(**defaults)

    def test_minimal_construction(self):
        s = self._make()
        assert s.backend == "kodo"
        assert s.lane == "claude_cli"
        assert s.sample_size == 10

    def test_defaults(self):
        s = self._make()
        assert s.task_type_scope == []
        assert s.risk_scope == []
        assert s.timeout_rate == 0.0
        assert s.validation_pass_rate == 0.0
        assert s.latency_class == LatencyClass.UNKNOWN
        assert s.median_duration_ms is None
        assert s.notes == ""

    def test_is_frozen(self):
        s = self._make()
        with pytest.raises(ValidationError):
            s.success_rate = 0.5

    def test_rates_bounded_0_to_1(self):
        with pytest.raises(ValidationError):
            self._make(success_rate=1.5)
        with pytest.raises(ValidationError):
            self._make(failure_rate=-0.1)


class TestStrategyFinding:
    def test_construction(self):
        f = StrategyFinding(
            category="reliability",
            summary="kodo is reliable.",
            evidence_strength=EvidenceStrength.STRONG,
        )
        assert f.category == "reliability"
        assert f.finding_id

    def test_defaults(self):
        f = StrategyFinding(
            category="x", summary="y", evidence_strength=EvidenceStrength.WEAK
        )
        assert f.affected_lanes == []
        assert f.affected_backends == []
        assert f.task_scope == []
        assert f.supporting_data == {}
        assert f.notes == ""

    def test_is_frozen(self):
        f = StrategyFinding(
            category="x", summary="y", evidence_strength=EvidenceStrength.WEAK
        )
        with pytest.raises(ValidationError):
            f.summary = "changed"

    def test_unique_finding_ids(self):
        f1 = StrategyFinding(category="x", summary="a", evidence_strength=EvidenceStrength.WEAK)
        f2 = StrategyFinding(category="x", summary="a", evidence_strength=EvidenceStrength.WEAK)
        assert f1.finding_id != f2.finding_id


class TestRoutingTuningProposal:
    def _make(self, **kw) -> RoutingTuningProposal:
        defaults = dict(
            summary="Adjust routing",
            proposed_change="Change backend preference",
            justification="Evidence shows low reliability",
            evidence_strength=EvidenceStrength.MODERATE,
            affected_policy_area="backend_preference",
        )
        defaults.update(kw)
        return RoutingTuningProposal(**defaults)

    def test_construction(self):
        p = self._make()
        assert p.summary == "Adjust routing"
        assert p.proposal_id

    def test_requires_review_is_always_true_by_default(self):
        p = self._make()
        assert p.requires_review is True

    def test_requires_review_cannot_be_false_in_phase_13(self):
        with pytest.raises(ValidationError):
            self._make(requires_review=False)

    def test_defaults(self):
        p = self._make()
        assert p.risk_notes == ""
        assert p.source_finding_ids == []
        assert p.policy_guardrails == []
        assert p.notes == ""

    def test_is_frozen(self):
        p = self._make()
        with pytest.raises(ValidationError):
            p.summary = "changed"

    def test_unique_proposal_ids(self):
        p1 = self._make()
        p2 = self._make()
        assert p1.proposal_id != p2.proposal_id


class TestStrategyAnalysisReport:
    def test_construction(self):
        r = StrategyAnalysisReport(record_count=10)
        assert r.record_count == 10
        assert r.active_policy_reference == "switchboard_current_policy"
        assert r.observed_evidence_source == "retained_execution_records"
        assert r.proposed_changes_status == "review_required"
        assert r.policy_guardrails_applied == []
        assert r.comparison_summaries == []
        assert r.findings == []
        assert r.recommendations == []
        assert r.limitations == []
        assert r.report_id
        assert r.generated_at is not None

    def test_is_frozen(self):
        r = StrategyAnalysisReport(record_count=0)
        with pytest.raises(ValidationError):
            r.record_count = 5

    def test_unique_report_ids(self):
        r1 = StrategyAnalysisReport(record_count=0)
        r2 = StrategyAnalysisReport(record_count=0)
        assert r1.report_id != r2.report_id
