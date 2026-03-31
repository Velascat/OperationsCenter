from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from control_plane.insights.derivers.commit_activity import CommitActivityDeriver
from control_plane.insights.derivers.dependency_drift import DependencyDriftDeriver
from control_plane.insights.derivers.dirty_tree import DirtyTreeDeriver
from control_plane.insights.derivers.file_hotspots import FileHotspotsDeriver
from control_plane.insights.derivers.observation_coverage import ObservationCoverageDeriver
from control_plane.insights.derivers.test_continuity import TestContinuityDeriver as ContinuityDeriver
from control_plane.insights.derivers.todo_concentration import TodoConcentrationDeriver
from control_plane.insights.loader import SnapshotLoader
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.artifact_writer import ObserverArtifactWriter
from control_plane.observer.models import (
    CommitMetadata,
    DependencyDriftSignal,
    FileHotspot,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TestSignal as ObserverTestSignal,
    TodoFileCount,
    TodoSignal,
)


def make_snapshot(
    *,
    run_id: str,
    observed_at: datetime,
    is_dirty: bool = False,
    commit_count: int = 0,
    hotspot: tuple[str, int] | None = None,
    test_status: str = "unknown",
    dependency_status: str = "not_available",
    todo_count: int = 0,
    fixme_count: int = 0,
    top_file: tuple[str, int] | None = None,
    collector_errors: dict[str, str] | None = None,
) -> RepoStateSnapshot:
    commits = [
        CommitMetadata(
            sha_short=f"abc123{i}",
            author="Test User" if i % 2 == 0 else "Other User",
            timestamp=observed_at - timedelta(minutes=i),
            subject=f"Commit {i}",
        )
        for i in range(commit_count)
    ]
    hotspots = [FileHotspot(path=hotspot[0], touch_count=hotspot[1])] if hotspot else []
    top_files = [TodoFileCount(path=top_file[0], count=top_file[1])] if top_file else []
    return RepoStateSnapshot(
        run_id=run_id,
        observed_at=observed_at,
        source_command="control-plane observe-repo",
        repo=RepoContextSnapshot(
            name="control-plane",
            path=Path("/tmp/control-plane"),
            current_branch="main",
            base_branch="main",
            is_dirty=is_dirty,
        ),
        signals=RepoSignalsSnapshot(
            recent_commits=commits,
            file_hotspots=hotspots,
            test_signal=ObserverTestSignal(status=test_status),
            dependency_drift=DependencyDriftSignal(status=dependency_status),
            todo_signal=TodoSignal(todo_count=todo_count, fixme_count=fixme_count, top_files=top_files),
        ),
        collector_errors=collector_errors or {},
    )


def test_loader_reads_latest_snapshot_with_bounded_history(tmp_path: Path) -> None:
    writer = ObserverArtifactWriter(tmp_path / "observer")
    oldest = make_snapshot(run_id="obs_old", observed_at=datetime(2026, 3, 31, 10, tzinfo=UTC))
    newest = make_snapshot(run_id="obs_new", observed_at=datetime(2026, 3, 31, 12, tzinfo=UTC))
    writer.write(oldest)
    writer.write(newest)

    snapshots = SnapshotLoader(tmp_path / "observer").load(repo="control-plane", snapshot_run_id=None, history_limit=1)

    assert [snapshot.run_id for snapshot in snapshots] == ["obs_new", "obs_old"]


def test_normalizer_assigns_deterministic_keys() -> None:
    insight = InsightNormalizer().normalize(
        kind="file_hotspot",
        subject="src/main.py",
        status="present",
        key_parts=["src/main.py", "dominant_current"],
        evidence={"current_touch_count": 5},
        first_seen_at=datetime(2026, 3, 31, 10, tzinfo=UTC),
        last_seen_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
    )
    assert insight.dedup_key == "file_hotspot|src/main.py|dominant_current"
    assert insight.insight_id == "file_hotspot:src/main.py:dominant_current"


def test_derivers_cover_bounded_insight_kinds() -> None:
    normalizer = InsightNormalizer()
    current = make_snapshot(
        run_id="obs_2",
        observed_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
        is_dirty=True,
        commit_count=4,
        hotspot=("src/control_plane/watcher.py", 6),
        test_status="failed",
        dependency_status="available",
        todo_count=4,
        fixme_count=1,
        top_file=("src/control_plane/watcher.py", 3),
        collector_errors={"dependency_drift": "timeout"},
    )
    previous = make_snapshot(
        run_id="obs_1",
        observed_at=datetime(2026, 3, 31, 11, tzinfo=UTC),
        is_dirty=True,
        commit_count=1,
        hotspot=("src/control_plane/watcher.py", 3),
        test_status="unknown",
        dependency_status="available",
        todo_count=2,
        fixme_count=0,
        top_file=("src/control_plane/watcher.py", 2),
        collector_errors={"dependency_drift": "timeout"},
    )
    snapshots = [current, previous]

    insights = []
    for deriver in [
        DirtyTreeDeriver(normalizer),
        CommitActivityDeriver(normalizer),
        FileHotspotsDeriver(normalizer),
        ContinuityDeriver(normalizer),
        DependencyDriftDeriver(normalizer),
        TodoConcentrationDeriver(normalizer),
        ObservationCoverageDeriver(normalizer),
    ]:
        insights.extend(deriver.derive(snapshots))

    dedup_keys = {insight.dedup_key for insight in insights}
    assert "dirty_tree|working_tree|dirty" in dedup_keys
    assert "commit_activity|recent_window" in dedup_keys
    assert "commit_activity|recent_window_changed" in dedup_keys
    assert "file_hotspot|src/control_plane/watcher.py|dominant_current" in dedup_keys
    assert "file_hotspot|src/control_plane/watcher.py|repeated_presence" in dedup_keys
    assert "test_status_continuity|unknown|failed|transition" in dedup_keys
    assert "dependency_drift_continuity|present|current" in dedup_keys
    assert "dependency_drift_continuity|present|persistent" in dedup_keys
    assert "todo_concentration|fixme|present" in dedup_keys
    assert "todo_concentration|todo_fixme_total|count_changed" in dedup_keys
    assert "observation_coverage|dependency_drift|persistent_unavailable" in dedup_keys


def test_observation_coverage_derives_unknown_test_signal() -> None:
    normalizer = InsightNormalizer()
    snapshot = make_snapshot(
        run_id="obs_1",
        observed_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
        test_status="unknown",
    )
    insights = ObservationCoverageDeriver(normalizer).derive([snapshot])

    assert {insight.dedup_key for insight in insights} == {
        "observation_coverage|dependency_drift|unavailable",
        "observation_coverage|test_signal|unavailable",
    }
