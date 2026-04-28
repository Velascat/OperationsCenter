"""Integration tests: observation_coverage deriver → rule pipeline."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.rules.observation_coverage import ObservationCoverageRule
from operations_center.insights.derivers.observation_coverage import ObservationCoverageDeriver
from operations_center.insights.normalizer import InsightNormalizer

from test_insights import make_snapshot


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

    def test_empty_snapshots_returns_no_insights_no_candidates(self) -> None:
        """Empty snapshot list → no insights, no candidates."""
        insights = ObservationCoverageDeriver(_normalizer()).derive([])
        assert insights == []
        candidates = ObservationCoverageRule(min_consecutive_runs=2).evaluate(insights)
        assert candidates == []

    def test_signal_available_mid_history_breaks_consecutive_count(self) -> None:
        """available → unavailable → unavailable: only 2 consecutive from the front."""
        t0 = datetime(2026, 4, 20, 12, tzinfo=UTC)
        snaps = [
            make_snapshot(run_id="obs_0", observed_at=t0, test_status="unknown", dependency_status="available"),
            make_snapshot(run_id="obs_1", observed_at=t0 - timedelta(hours=1), test_status="unknown", dependency_status="available"),
            make_snapshot(run_id="obs_2", observed_at=t0 - timedelta(hours=2), test_status="discoverable", dependency_status="available"),
        ]
        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        test_insights = [i for i in insights if i.subject == "test_signal"]
        assert len(test_insights) == 1
        assert int(test_insights[0].evidence["consecutive_snapshots"]) == 2

    def test_both_signals_unavailable_simultaneously(self) -> None:
        """Both test_signal and dependency_drift unavailable → candidates for both."""
        t0 = datetime(2026, 4, 20, 12, tzinfo=UTC)
        snaps = [
            make_snapshot(run_id=f"obs_{i}", observed_at=t0 - timedelta(hours=i), test_status="unknown", dependency_status="not_available")
            for i in range(3)
        ]
        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        candidates = ObservationCoverageRule(min_consecutive_runs=2).evaluate(insights)
        subjects = {c.subject for c in candidates}
        assert "test_signal" in subjects
        assert "dependency_drift" in subjects


class TestObservationCoverageRuleFiltering:
    """Tests for ObservationCoverageRule edge cases."""

    def test_wrong_kind_filtered_out(self) -> None:
        """Insights with wrong kind are ignored."""
        from operations_center.insights.models import DerivedInsight
        ts = datetime(2026, 4, 20, 12, tzinfo=UTC)
        wrong_kind = DerivedInsight(
            insight_id="wrong:kind",
            dedup_key="observation_coverage|test_signal|persistent_unavailable",
            kind="dependency_drift_continuity",  # wrong kind
            subject="test_signal",
            status="present",
            evidence={"signal": "test_signal", "consecutive_snapshots": 5},
            first_seen_at=ts - timedelta(hours=1),
            last_seen_at=ts,
        )
        candidates = ObservationCoverageRule(min_consecutive_runs=2).evaluate([wrong_kind])
        assert candidates == []

    def test_non_persistent_unavailable_suffix_filtered_out(self) -> None:
        """Insights with 'unavailable' (not 'persistent_unavailable') suffix are skipped."""
        from operations_center.insights.models import DerivedInsight
        ts = datetime(2026, 4, 20, 12, tzinfo=UTC)
        single_unavailable = DerivedInsight(
            insight_id="single:unavail",
            dedup_key="observation_coverage|test_signal|unavailable",
            kind="observation_coverage",
            subject="test_signal",
            status="present",
            evidence={"signal": "test_signal", "consecutive_snapshots": 1},
            first_seen_at=ts,
            last_seen_at=ts,
        )
        candidates = ObservationCoverageRule(min_consecutive_runs=2).evaluate([single_unavailable])
        assert candidates == []

    def test_below_threshold_no_candidate(self) -> None:
        """persistent_unavailable with consecutive_snapshots below threshold → no candidate."""
        from operations_center.insights.models import DerivedInsight
        ts = datetime(2026, 4, 20, 12, tzinfo=UTC)
        below = DerivedInsight(
            insight_id="below:thresh",
            dedup_key="observation_coverage|test_signal|persistent_unavailable",
            kind="observation_coverage",
            subject="test_signal",
            status="present",
            evidence={"signal": "test_signal", "consecutive_snapshots": 1},
            first_seen_at=ts,
            last_seen_at=ts,
        )
        candidates = ObservationCoverageRule(min_consecutive_runs=3).evaluate([below])
        assert candidates == []
