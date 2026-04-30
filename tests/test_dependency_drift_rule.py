# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the DependencyDriftRule decision rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.rules.dependency_drift import DependencyDriftRule
from operations_center.insights.models import DerivedInsight


def make_insight(
    *,
    kind: str = "dependency_drift_continuity",
    subject: str = "dependency_drift",
    dedup_key: str = "dependency_drift_continuity|present|persistent",
    evidence: dict[str, object] | None = None,
) -> DerivedInsight:
    ts = datetime(2026, 3, 31, 12, tzinfo=UTC)
    return DerivedInsight(
        insight_id=dedup_key.replace("|", ":"),
        dedup_key=dedup_key,
        kind=kind,
        subject=subject,
        status="present",
        evidence=evidence or {},
        first_seen_at=ts - timedelta(hours=1),
        last_seen_at=ts,
    )


class TestDependencyDriftRule:
    """Tests for DependencyDriftRule.evaluate()."""

    def test_no_insights_returns_empty(self) -> None:
        rule = DependencyDriftRule(min_consecutive_runs=3)
        assert rule.evaluate([]) == []

    def test_wrong_kind_filtered_out(self) -> None:
        rule = DependencyDriftRule(min_consecutive_runs=3)
        insight = make_insight(
            kind="observation_coverage",
            subject="dependency_drift",
            dedup_key="observation_coverage|present|persistent",
            evidence={"consecutive_snapshots": 5},
        )
        assert rule.evaluate([insight]) == []

    def test_wrong_subject_filtered_out(self) -> None:
        rule = DependencyDriftRule(min_consecutive_runs=3)
        insight = make_insight(
            kind="dependency_drift_continuity",
            subject="test_signal",
            dedup_key="dependency_drift_continuity|present|persistent",
            evidence={"consecutive_snapshots": 5},
        )
        assert rule.evaluate([insight]) == []

    def test_present_persistent_generates_candidate(self) -> None:
        """Insight with 'present|persistent' dedup_key suffix generates a candidate."""
        rule = DependencyDriftRule(min_consecutive_runs=3)
        insight = make_insight(
            evidence={"consecutive_snapshots": 3},
        )
        candidates = rule.evaluate([insight])
        assert len(candidates) == 1
        assert isinstance(candidates[0], CandidateSpec)

    def test_persistent_high_consecutive_generates_high_confidence(self) -> None:
        """Insight with >=4 consecutive snapshots generates a high-confidence candidate."""
        rule = DependencyDriftRule(min_consecutive_runs=3)
        insight = make_insight(
            evidence={"consecutive_snapshots": 5},
        )
        candidates = rule.evaluate([insight])
        assert len(candidates) == 1
        assert candidates[0].confidence == "high"

    def test_transition_dedup_key_does_not_match(self) -> None:
        """Insight with a transition dedup_key suffix is not matched by the rule."""
        rule = DependencyDriftRule(min_consecutive_runs=3)
        insight = make_insight(
            dedup_key="dependency_drift_continuity|available|not_available|transition",
            evidence={"consecutive_snapshots": 5},
        )
        assert rule.evaluate([insight]) == []

    def test_below_min_consecutive_runs_returns_empty(self) -> None:
        rule = DependencyDriftRule(min_consecutive_runs=3)
        insight = make_insight(
            evidence={"consecutive_snapshots": 2},
        )
        assert rule.evaluate([insight]) == []

    def test_candidate_spec_fields(self) -> None:
        """Verify that CandidateSpec fields are correctly populated."""
        rule = DependencyDriftRule(min_consecutive_runs=2)
        insight = make_insight(
            evidence={"consecutive_snapshots": 3},
        )
        candidates = rule.evaluate([insight])
        assert len(candidates) == 1
        spec = candidates[0]

        assert spec.family == "dependency_drift_followup"
        assert spec.subject == "dependency_drift"
        assert spec.pattern_key == "present_persistent"
        assert spec.evidence == {"consecutive_snapshots": 3}
        assert "dependency_drift_persistent_min_consecutive_runs" in spec.matched_rules
        assert "candidate_not_seen_in_cooldown_window" in spec.matched_rules
        assert spec.confidence == "medium"
        assert spec.risk_class == "logic"
        assert spec.expires_after_runs == 5
        assert spec.priority == (2, 0, "dependency_drift_followup|present_persistent")
        assert any("3 consecutive" in line for line in spec.evidence_lines)
        assert spec.proposal_outline.title_hint is not None
        assert spec.proposal_outline.summary_hint is not None
        assert spec.proposal_outline.source_family == "dependency_drift_followup"

    def test_medium_confidence_below_four_snapshots(self) -> None:
        """Exactly 3 consecutive snapshots yields medium confidence."""
        rule = DependencyDriftRule(min_consecutive_runs=3)
        insight = make_insight(
            evidence={"consecutive_snapshots": 3},
        )
        candidates = rule.evaluate([insight])
        assert len(candidates) == 1
        assert candidates[0].confidence == "medium"
