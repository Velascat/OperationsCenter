# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Integration tests: observation_coverage deriver → rule pipeline."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.rules.observation_coverage import ObservationCoverageRule
from operations_center.insights.derivers.observation_coverage import ObservationCoverageDeriver
from operations_center.insights.normalizer import InsightNormalizer

from test_insights import _make_snapshot as make_snapshot


def _normalizer() -> InsightNormalizer:
    return InsightNormalizer()


class TestObservationCoveragePipeline:
    """Wire deriver → rule and verify CandidateSpecs come out."""

    def test_repeated_unknown_test_signal_produces_candidate(self) -> None:
        """3 consecutive unknown test_status snapshots → at least one CandidateSpec."""
        t0 = datetime(2026, 4, 20, 12, tzinfo=UTC)
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=t0 - timedelta(hours=i),
                test_status="unknown",
                dependency_status="available",
            )
            for i in range(3)
        ]

        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        candidates = ObservationCoverageRule(min_consecutive_runs=2).evaluate(insights)

        assert len(candidates) >= 1
        assert all(isinstance(c, CandidateSpec) for c in candidates)

        test_candidates = [c for c in candidates if c.subject == "test_signal"]
        assert len(test_candidates) == 1

        spec = test_candidates[0]
        assert "test_signal" in spec.proposal_outline.title_hint
        assert spec.proposal_outline.title_hint == (
            "Restore repeated missing test_signal coverage"
        )
        assert spec.confidence == "high"  # 3 consecutive → high

    def test_dependency_drift_flows_through_pipeline(self) -> None:
        """3 consecutive not_available dependency_status → dependency_drift candidate."""
        t0 = datetime(2026, 4, 20, 12, tzinfo=UTC)
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=t0 - timedelta(hours=i),
                test_status="discoverable",
                dependency_status="not_available",
            )
            for i in range(3)
        ]

        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        candidates = ObservationCoverageRule(min_consecutive_runs=2).evaluate(insights)

        assert len(candidates) >= 1

        dep_candidates = [c for c in candidates if c.subject == "dependency_drift"]
        assert len(dep_candidates) == 1

        spec = dep_candidates[0]
        assert "dependency_drift" in spec.proposal_outline.title_hint
        assert spec.proposal_outline.title_hint == (
            "Restore repeated missing dependency_drift coverage"
        )
