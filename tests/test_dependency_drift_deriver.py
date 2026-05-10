# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for DependencyDriftDeriver."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.insights.derivers.dependency_drift import DependencyDriftDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    CheckSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    TodoSignal,
)


def _normalizer() -> InsightNormalizer:
    return InsightNormalizer()


def _make_snapshot(
    *,
    dependency_drift_status: str = "not_available",
    observed_at: datetime | None = None,
) -> RepoStateSnapshot:
    now = observed_at or datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status=dependency_drift_status),
        todo_signal=TodoSignal(),
        architecture_signal=ArchitectureSignal(status="unavailable"),
        benchmark_signal=BenchmarkSignal(status="unavailable"),
        security_signal=SecuritySignal(status="unavailable"),
    )
    return RepoStateSnapshot(
        run_id="obs_test_001",
        observed_at=now,
        source_command="test",
        repo=RepoContextSnapshot(
            name="test-repo",
            path=Path("/tmp/test-repo"),
            current_branch="main",
            base_branch="main",
            is_dirty=False,
        ),
        signals=signals,
    )


class TestDependencyDriftDeriver:
    def test_empty_snapshots(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        assert deriver.derive([]) == []

    def test_single_available_produces_current_insight(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        snap = _make_snapshot(dependency_drift_status="available")
        insights = deriver.derive([snap])
        assert len(insights) == 1
        assert insights[0].kind == "dependency_drift_continuity"
        assert "current" in insights[0].dedup_key
        assert insights[0].evidence["current_status"] == "available"

    def test_two_available_produces_current_and_persistent(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(dependency_drift_status="available", observed_at=newer)
        snap_older = _make_snapshot(dependency_drift_status="available", observed_at=older)
        # snapshots[0] is the most recent
        insights = deriver.derive([snap_recent, snap_older])
        assert len(insights) == 2
        dedup_keys = {i.dedup_key for i in insights}
        assert any("current" in k for k in dedup_keys)
        assert any("persistent" in k for k in dedup_keys)
        persistent = [i for i in insights if "persistent" in i.dedup_key][0]
        assert persistent.evidence["consecutive_snapshots"] == 2

    def test_transition_available_to_not_available(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        # snapshots[0] is most recent (not_available), snapshots[1] was available
        snap_recent = _make_snapshot(dependency_drift_status="not_available", observed_at=newer)
        snap_older = _make_snapshot(dependency_drift_status="available", observed_at=older)
        insights = deriver.derive([snap_recent, snap_older])
        assert len(insights) == 1
        assert "transition" in insights[0].dedup_key
        assert insights[0].evidence["previous_status"] == "available"
        assert insights[0].evidence["current_status"] == "not_available"

    def test_single_not_available_no_insights(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        snap = _make_snapshot(dependency_drift_status="not_available")
        assert deriver.derive([snap]) == []

    def test_timestamps_first_and_last_seen(self) -> None:
        deriver = DependencyDriftDeriver(_normalizer())
        newer = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
        older = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
        snap_recent = _make_snapshot(dependency_drift_status="available", observed_at=newer)
        snap_older = _make_snapshot(dependency_drift_status="available", observed_at=older)
        insights = deriver.derive([snap_recent, snap_older])
        current = [i for i in insights if "current" in i.dedup_key][0]
        persistent = [i for i in insights if "persistent" in i.dedup_key][0]
        assert current.first_seen_at == older
        assert current.last_seen_at == newer
        assert persistent.first_seen_at == older
        assert persistent.last_seen_at == newer
