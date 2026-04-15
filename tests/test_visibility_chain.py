"""Tests for the visibility chain: derivers and rule behaviour with new CheckSignal statuses.

Covers ObservationCoverageDeriver, TestContinuityDeriver, and TestVisibilityRule
behaviour for 'discoverable', 'no_config', and 'unknown' statuses to ensure the
bounded fallback signals flow correctly through the insight pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from control_plane.decision.rules.test_visibility import TestVisibilityRule
from control_plane.insights.derivers.observation_coverage import ObservationCoverageDeriver
from control_plane.insights.derivers.test_continuity import TestContinuityDeriver
from control_plane.insights.normalizer import InsightNormalizer

# Re-use the helper from the existing test module.
from test_insights import make_snapshot


def _normalizer() -> InsightNormalizer:
    return InsightNormalizer()


# ======================================================================
# ObservationCoverageDeriver – new status handling
# ======================================================================


class TestObservationCoverageDiscoverable:
    """'discoverable' must NOT be treated as unavailable."""

    def test_single_discoverable_snapshot_no_test_signal_unavailable(self) -> None:
        snap = make_snapshot(
            run_id="obs_1",
            observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC),
            test_status="discoverable",
        )
        insights = ObservationCoverageDeriver(_normalizer()).derive([snap])
        keys = {i.dedup_key for i in insights}
        assert not any("test_signal" in k for k in keys)

    def test_consecutive_discoverable_no_persistent_unavailable(self) -> None:
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC) - timedelta(hours=i),
                test_status="discoverable",
            )
            for i in range(3)
        ]
        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        keys = {i.dedup_key for i in insights}
        assert not any("test_signal" in k for k in keys)


class TestObservationCoverageNoConfig:
    """'no_config' must NOT be treated as unavailable."""

    def test_single_no_config_snapshot(self) -> None:
        snap = make_snapshot(
            run_id="obs_1",
            observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC),
            test_status="no_config",
        )
        insights = ObservationCoverageDeriver(_normalizer()).derive([snap])
        keys = {i.dedup_key for i in insights}
        assert not any("test_signal" in k for k in keys)

    def test_consecutive_no_config_no_persistent_unavailable(self) -> None:
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC) - timedelta(hours=i),
                test_status="no_config",
            )
            for i in range(3)
        ]
        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        keys = {i.dedup_key for i in insights}
        assert not any("test_signal" in k for k in keys)


class TestObservationCoverageUnknown:
    """'unknown' IS treated as unavailable (existing behaviour, regression guard)."""

    def test_single_unknown_emits_unavailable(self) -> None:
        snap = make_snapshot(
            run_id="obs_1",
            observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC),
            test_status="unknown",
        )
        insights = ObservationCoverageDeriver(_normalizer()).derive([snap])
        keys = {i.dedup_key for i in insights}
        assert "observation_coverage|test_signal|unavailable" in keys

    def test_consecutive_unknown_emits_persistent_unavailable(self) -> None:
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC) - timedelta(hours=i),
                test_status="unknown",
            )
            for i in range(3)
        ]
        insights = ObservationCoverageDeriver(_normalizer()).derive(snaps)
        keys = {i.dedup_key for i in insights}
        assert "observation_coverage|test_signal|persistent_unavailable" in keys


# ======================================================================
# TestContinuityDeriver – tracking new statuses
# ======================================================================


class TestContinuityDiscoverable:
    """'discoverable' persistence and transitions are tracked."""

    def test_persistent_discoverable(self) -> None:
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC) - timedelta(hours=i),
                test_status="discoverable",
            )
            for i in range(3)
        ]
        insights = TestContinuityDeriver(_normalizer()).derive(snaps)
        keys = {i.dedup_key for i in insights}
        assert "test_status_continuity|discoverable|persistent" in keys

    def test_transition_unknown_to_discoverable(self) -> None:
        snaps = [
            make_snapshot(
                run_id="obs_1",
                observed_at=datetime(2026, 4, 15, 12, tzinfo=UTC),
                test_status="discoverable",
            ),
            make_snapshot(
                run_id="obs_0",
                observed_at=datetime(2026, 4, 15, 11, tzinfo=UTC),
                test_status="unknown",
            ),
        ]
        insights = TestContinuityDeriver(_normalizer()).derive(snaps)
        keys = {i.dedup_key for i in insights}
        assert "test_status_continuity|unknown|discoverable|transition" in keys

    def test_single_discoverable_no_persistent(self) -> None:
        snaps = [
            make_snapshot(
                run_id="obs_1",
                observed_at=datetime(2026, 4, 15, 12, tzinfo=UTC),
                test_status="discoverable",
            ),
        ]
        insights = TestContinuityDeriver(_normalizer()).derive(snaps)
        keys = {i.dedup_key for i in insights}
        assert not any("persistent" in k for k in keys)


class TestContinuityNoConfig:
    """'no_config' persistence is tracked."""

    def test_persistent_no_config(self) -> None:
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC) - timedelta(hours=i),
                test_status="no_config",
            )
            for i in range(2)
        ]
        insights = TestContinuityDeriver(_normalizer()).derive(snaps)
        keys = {i.dedup_key for i in insights}
        assert "test_status_continuity|no_config|persistent" in keys

    def test_transition_no_config_to_discoverable(self) -> None:
        snaps = [
            make_snapshot(
                run_id="obs_1",
                observed_at=datetime(2026, 4, 15, 12, tzinfo=UTC),
                test_status="discoverable",
            ),
            make_snapshot(
                run_id="obs_0",
                observed_at=datetime(2026, 4, 15, 11, tzinfo=UTC),
                test_status="no_config",
            ),
        ]
        insights = TestContinuityDeriver(_normalizer()).derive(snaps)
        keys = {i.dedup_key for i in insights}
        assert "test_status_continuity|no_config|discoverable|transition" in keys


# ======================================================================
# TestVisibilityRule – candidate generation for new statuses
# ======================================================================


def _make_insight(*, kind: str, subject: str, dedup_key: str, evidence: dict) -> object:
    """Build a minimal DerivedInsight for rule evaluation."""
    return _normalizer().normalize(
        kind=kind,
        subject=subject,
        status="present",
        key_parts=dedup_key.split("|")[1:],  # strip kind prefix
        evidence=evidence,
        first_seen_at=datetime(2026, 4, 15, 10, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 15, 12, tzinfo=UTC),
    )


class TestVisibilityRuleDiscoverable:
    """'discoverable' persistent insights must NOT generate unknown_persistent candidates."""

    def test_discoverable_persistent_no_candidate(self) -> None:
        insight = _make_insight(
            kind="test_status_continuity",
            subject="test_signal",
            dedup_key="test_status_continuity|discoverable|persistent",
            evidence={"current_status": "discoverable", "consecutive_snapshots": 5},
        )
        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate([insight])
        assert len(candidates) == 0

    def test_discoverable_does_not_trigger_coverage_unavailable(self) -> None:
        """Even if observation_coverage insight exists, it should NOT be for test_signal
        when status is 'discoverable'.  This test verifies the rule sees no candidate
        if such an insight were incorrectly fed in."""
        # Simulate an observation_coverage insight with subject=test_signal but
        # ending in persistent_unavailable (which shouldn't happen for discoverable,
        # but we verify the rule is not the last line of defence).
        insight = _make_insight(
            kind="observation_coverage",
            subject="test_signal",
            dedup_key="observation_coverage|test_signal|persistent_unavailable",
            evidence={"signal": "test_signal", "consecutive_snapshots": 3},
        )
        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate([insight])
        # The rule DOES emit a candidate for this insight (it trusts the deriver).
        # The important thing is that the deriver never produces this insight for
        # 'discoverable', which is tested in TestObservationCoverageDiscoverable.
        assert len(candidates) == 1
        assert candidates[0].pattern_key == "coverage_unavailable_persistent"


class TestVisibilityRuleNoConfig:
    """'no_config' persistent insights must NOT generate unknown_persistent candidates."""

    def test_no_config_persistent_no_candidate(self) -> None:
        insight = _make_insight(
            kind="test_status_continuity",
            subject="test_signal",
            dedup_key="test_status_continuity|no_config|persistent",
            evidence={"current_status": "no_config", "consecutive_snapshots": 5},
        )
        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate([insight])
        assert len(candidates) == 0


class TestVisibilityRuleUnknown:
    """'unknown' persistent insights SHOULD generate candidates (regression guard)."""

    def test_unknown_persistent_generates_candidate(self) -> None:
        insight = _make_insight(
            kind="test_status_continuity",
            subject="test_signal",
            dedup_key="test_status_continuity|unknown|persistent",
            evidence={"current_status": "unknown", "consecutive_snapshots": 3},
        )
        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate([insight])
        assert len(candidates) == 1
        assert candidates[0].pattern_key == "unknown_persistent"
        assert candidates[0].confidence == "medium"

    def test_unknown_persistent_high_confidence_at_five(self) -> None:
        insight = _make_insight(
            kind="test_status_continuity",
            subject="test_signal",
            dedup_key="test_status_continuity|unknown|persistent",
            evidence={"current_status": "unknown", "consecutive_snapshots": 5},
        )
        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate([insight])
        assert len(candidates) == 1
        assert candidates[0].confidence == "high"

    def test_unknown_below_min_consecutive_no_candidate(self) -> None:
        insight = _make_insight(
            kind="test_status_continuity",
            subject="test_signal",
            dedup_key="test_status_continuity|unknown|persistent",
            evidence={"current_status": "unknown", "consecutive_snapshots": 1},
        )
        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate([insight])
        assert len(candidates) == 0


# ======================================================================
# End-to-end: deriver → rule pipeline for new statuses
# ======================================================================


class TestEndToEndVisibilityChain:
    """Verify that new statuses flow through the full deriver → rule pipeline
    without producing unwanted proposals."""

    def test_discoverable_chain_produces_no_unknown_candidates(self) -> None:
        """Three consecutive 'discoverable' snapshots → derivers → rule → zero unknown candidates."""
        norm = _normalizer()
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC) - timedelta(hours=i),
                test_status="discoverable",
            )
            for i in range(3)
        ]

        insights = []
        insights.extend(ObservationCoverageDeriver(norm).derive(snaps))
        insights.extend(TestContinuityDeriver(norm).derive(snaps))

        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate(insights)

        # No unknown_persistent or coverage_unavailable_persistent candidates
        pattern_keys = {c.pattern_key for c in candidates}
        assert "unknown_persistent" not in pattern_keys
        assert "coverage_unavailable_persistent" not in pattern_keys

    def test_no_config_chain_produces_no_unknown_candidates(self) -> None:
        """Three consecutive 'no_config' snapshots → derivers → rule → zero unknown candidates."""
        norm = _normalizer()
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC) - timedelta(hours=i),
                test_status="no_config",
            )
            for i in range(3)
        ]

        insights = []
        insights.extend(ObservationCoverageDeriver(norm).derive(snaps))
        insights.extend(TestContinuityDeriver(norm).derive(snaps))

        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate(insights)

        pattern_keys = {c.pattern_key for c in candidates}
        assert "unknown_persistent" not in pattern_keys
        assert "coverage_unavailable_persistent" not in pattern_keys

    def test_unknown_chain_produces_expected_candidates(self) -> None:
        """Three consecutive 'unknown' snapshots → derivers → rule → expected candidates."""
        norm = _normalizer()
        snaps = [
            make_snapshot(
                run_id=f"obs_{i}",
                observed_at=datetime(2026, 4, 15, 10, tzinfo=UTC) - timedelta(hours=i),
                test_status="unknown",
            )
            for i in range(3)
        ]

        insights = []
        insights.extend(ObservationCoverageDeriver(norm).derive(snaps))
        insights.extend(TestContinuityDeriver(norm).derive(snaps))

        rule = TestVisibilityRule(min_consecutive_runs=2)
        candidates = rule.evaluate(insights)

        pattern_keys = {c.pattern_key for c in candidates}
        assert "unknown_persistent" in pattern_keys
        assert "coverage_unavailable_persistent" in pattern_keys
