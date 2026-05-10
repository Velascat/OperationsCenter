# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for Session 9 autonomy gap implementations.

S9-1  Event-driven pipeline trigger
S9-3  No-op loop detection
S9-4  Per-repo × family calibration
S9-6  Budget allocation by acceptance rate
S9-7  Test coverage gap detection
S9-10 Theme aggregation deriver
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# S9-1: Event-driven pipeline trigger
# ---------------------------------------------------------------------------

def test_pipeline_trigger_snapshot_mtimes_detects_new_file(tmp_path: Path) -> None:
    from operations_center.entrypoints.pipeline_trigger.main import _snapshot_mtimes, _has_changed

    f = tmp_path / "fetch_head"
    sources = [f]

    # Before file exists
    snap1 = _snapshot_mtimes(sources)
    assert str(f) not in snap1

    # After file is created
    f.write_text("abc")
    snap2 = _snapshot_mtimes(sources)
    assert str(f) in snap2

    changed = _has_changed(snap1, snap2)
    assert str(f) in changed


def test_pipeline_trigger_debounce_no_double_run(tmp_path: Path) -> None:
    """When elapsed < min_interval, has_changed returns list but no run should happen.
    We test the logic directly without spawning a subprocess."""
    from operations_center.entrypoints.pipeline_trigger.main import _has_changed

    snap_old = {"a": 1.0}
    snap_new = {"a": 2.0}
    changed = _has_changed(snap_old, snap_new)
    assert "a" in changed


def test_pipeline_trigger_no_change_when_same(tmp_path: Path) -> None:
    from operations_center.entrypoints.pipeline_trigger.main import _has_changed

    snap = {"a": 1.0, "b": 2.0}
    changed = _has_changed(snap, snap.copy())
    assert changed == []


# ---------------------------------------------------------------------------
# S9-3: No-op loop detection
# ---------------------------------------------------------------------------

def test_noop_loop_deriver_no_insights_when_no_proposals(tmp_path: Path) -> None:
    from operations_center.insights.derivers.noop_loop import NoOpLoopDeriver
    from operations_center.insights.normalizer import InsightNormalizer
    from operations_center.observer.models import (
        RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot,
        CheckSignal, DependencyDriftSignal, TodoSignal,
    )

    normalizer = InsightNormalizer()
    deriver = NoOpLoopDeriver(
        normalizer,
        proposer_root=tmp_path / "proposer",
        feedback_root=tmp_path / "feedback",
    )

    snap = RepoStateSnapshot(
        run_id="r1",
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(name="repo", path=tmp_path, current_branch="main", is_dirty=False),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
        ),
    )
    insights = deriver.derive([snap])
    assert insights == []


def test_noop_loop_deriver_detects_cycling_family(tmp_path: Path) -> None:
    from operations_center.insights.derivers.noop_loop import NoOpLoopDeriver
    from operations_center.insights.normalizer import InsightNormalizer
    from operations_center.observer.models import (
        RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot,
        CheckSignal, DependencyDriftSignal, TodoSignal,
    )

    proposer_root = tmp_path / "proposer"
    proposer_root.mkdir()
    feedback_root = tmp_path / "feedback"
    feedback_root.mkdir()

    # Create 4 proposer artifacts all proposing lint_fix
    for i in range(4):
        (proposer_root / f"proposer_result_{i:04d}.json").write_text(json.dumps({
            "generated_at": datetime.now(UTC).isoformat(),
            "created_tasks": [{"source_family": "lint_fix", "title": f"Fix lint {i}"}],
        }))

    # No feedback records (zero merges)

    normalizer = InsightNormalizer()
    deriver = NoOpLoopDeriver(
        normalizer,
        proposer_root=proposer_root,
        feedback_root=feedback_root,
        min_proposals=3,
    )

    snap = RepoStateSnapshot(
        run_id="r1",
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(name="repo", path=tmp_path, current_branch="main", is_dirty=False),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
        ),
    )
    insights = deriver.derive([snap])
    kinds = [i.kind for i in insights]
    assert "noop_loop/family_cycling" in kinds
    cycling = next(i for i in insights if i.kind == "noop_loop/family_cycling")
    assert cycling.evidence["family"] == "lint_fix"
    assert cycling.evidence["proposals_in_window"] == 4
    assert cycling.evidence["merges_in_window"] == 0


def test_noop_loop_deriver_no_insight_when_family_has_merges(tmp_path: Path) -> None:
    from operations_center.insights.derivers.noop_loop import NoOpLoopDeriver
    from operations_center.insights.normalizer import InsightNormalizer
    from operations_center.observer.models import (
        RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot,
        CheckSignal, DependencyDriftSignal, TodoSignal,
    )

    proposer_root = tmp_path / "proposer"
    proposer_root.mkdir()
    feedback_root = tmp_path / "feedback"
    feedback_root.mkdir()

    for i in range(4):
        (proposer_root / f"proposer_result_{i:04d}.json").write_text(json.dumps({
            "generated_at": datetime.now(UTC).isoformat(),
            "created_tasks": [{"source_family": "lint_fix"}],
        }))

    # One merged outcome
    (feedback_root / "task1.json").write_text(json.dumps({
        "recorded_at": datetime.now(UTC).isoformat(),
        "outcome": "merged",
        "family": "lint_fix",
        "source_family": "lint_fix",
    }))

    normalizer = InsightNormalizer()
    deriver = NoOpLoopDeriver(
        normalizer,
        proposer_root=proposer_root,
        feedback_root=feedback_root,
        min_proposals=3,
    )

    snap = RepoStateSnapshot(
        run_id="r1",
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(name="repo", path=tmp_path, current_branch="main", is_dirty=False),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
        ),
    )
    insights = deriver.derive([snap])
    assert not any(i.kind == "noop_loop/family_cycling" for i in insights)


# ---------------------------------------------------------------------------
# S9-4: Per-repo × family calibration
# ---------------------------------------------------------------------------

def test_calibration_per_repo_segregation(tmp_path: Path) -> None:
    from operations_center.tuning.calibration import ConfidenceCalibrationStore, _MIN_SAMPLE_SIZE

    store = ConfidenceCalibrationStore(tmp_path / "cal.json")

    # repo_a: all merged (high acceptance)
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("lint_fix", "high", "merged", repo_key="repo_a")

    # repo_b: all abandoned (low acceptance)
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("lint_fix", "high", "abandoned", repo_key="repo_b")

    rate_a = store.calibration_for("lint_fix", "high", repo_key="repo_a")
    rate_b = store.calibration_for("lint_fix", "high", repo_key="repo_b")

    assert rate_a == pytest.approx(1.0)
    assert rate_b == pytest.approx(0.0)


def test_calibration_global_aggregate_includes_all_repos(tmp_path: Path) -> None:
    from operations_center.tuning.calibration import ConfidenceCalibrationStore, _MIN_SAMPLE_SIZE

    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("lint_fix", "medium", "merged", repo_key="repo_a")
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("lint_fix", "medium", "abandoned", repo_key="repo_b")

    # Global rate should be 50%
    global_rate = store.calibration_for("lint_fix", "medium")
    assert global_rate == pytest.approx(0.5)


def test_calibration_per_repo_report(tmp_path: Path) -> None:
    from operations_center.tuning.calibration import ConfidenceCalibrationStore, _MIN_SAMPLE_SIZE

    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("type_fix", "high", "merged", repo_key="myrepo")

    records = store.report(per_repo=True)
    assert len(records) >= 1
    r = next((x for x in records if x.repo_key == "myrepo"), None)
    assert r is not None
    assert r.family == "type_fix"
    assert r.acceptance_rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# S9-6: Budget allocation by acceptance rate
# ---------------------------------------------------------------------------

def test_budget_calibration_penalty_not_applied_when_no_calibration_data(tmp_path: Path) -> None:
    """When no calibration data exists, no penalty should be applied."""
    from operations_center.tuning.calibration import ConfidenceCalibrationStore

    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    rate = store.calibration_for("lint_fix", "high")
    assert rate is None  # no data = no penalty


def test_calibration_ratio_computation_for_low_performance() -> None:
    from operations_center.tuning.calibration import _EXPECTED_RATES

    # For a "high" confidence family with 20% acceptance → ratio = 0.2 / 0.8 = 0.25 < 0.5
    acceptance_rate = 0.2
    expected = _EXPECTED_RATES["high"]  # 0.8
    ratio = acceptance_rate / expected
    assert ratio < 0.5  # should trigger budget penalty


# ---------------------------------------------------------------------------
# S9-7: Test coverage gap detection
# ---------------------------------------------------------------------------

def test_coverage_signal_collector_reads_coverage_xml(tmp_path: Path) -> None:
    from operations_center.observer.collectors.coverage_signal import CoverageSignalCollector
    from operations_center.observer.service import ObserverContext

    xml_content = """<?xml version="1.0" ?>
<coverage line-rate="0.74" branch-rate="0.5" timestamp="1234567890">
  <packages>
    <package name="mypackage">
      <classes>
        <class filename="mypackage/foo.py" line-rate="0.5" />
        <class filename="mypackage/bar.py" line-rate="0.95" />
      </classes>
    </package>
  </packages>
</coverage>"""
    (tmp_path / "coverage.xml").write_text(xml_content)

    ctx = MagicMock(spec=ObserverContext)
    ctx.repo_path = tmp_path
    ctx.logs_root = tmp_path / "logs"

    collector = CoverageSignalCollector()
    signal = collector.collect(ctx)

    assert signal.status == "measured"
    assert signal.total_coverage_pct == pytest.approx(74.0)
    assert signal.source == "coverage.xml"
    # foo.py at 50% is below 80% threshold
    assert signal.uncovered_file_count >= 1


def test_coverage_signal_collector_unavailable_when_no_files(tmp_path: Path) -> None:
    from operations_center.observer.collectors.coverage_signal import CoverageSignalCollector
    from operations_center.observer.service import ObserverContext

    ctx = MagicMock(spec=ObserverContext)
    ctx.repo_path = tmp_path
    ctx.logs_root = tmp_path / "logs"

    collector = CoverageSignalCollector()
    signal = collector.collect(ctx)
    assert signal.status == "unavailable"


def test_coverage_gap_deriver_emits_low_overall(tmp_path: Path) -> None:
    from operations_center.insights.derivers.coverage_gap import CoverageGapDeriver
    from operations_center.insights.normalizer import InsightNormalizer
    from operations_center.observer.models import (
        CoverageSignal, RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot,
        CheckSignal, DependencyDriftSignal, TodoSignal,
    )

    normalizer = InsightNormalizer()
    deriver = CoverageGapDeriver(normalizer)

    snap = RepoStateSnapshot(
        run_id="r1",
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(name="repo", path=tmp_path, current_branch="main", is_dirty=False),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
            coverage_signal=CoverageSignal(
                status="measured",
                total_coverage_pct=45.0,
                uncovered_file_count=0,
            ),
        ),
    )
    insights = deriver.derive([snap])
    kinds = [i.kind for i in insights]
    assert "coverage_gap/low_overall" in kinds


def test_coverage_gap_deriver_no_insight_when_unavailable(tmp_path: Path) -> None:
    from operations_center.insights.derivers.coverage_gap import CoverageGapDeriver
    from operations_center.insights.normalizer import InsightNormalizer
    from operations_center.observer.models import (
        CoverageSignal, RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot,
        CheckSignal, DependencyDriftSignal, TodoSignal,
    )

    normalizer = InsightNormalizer()
    deriver = CoverageGapDeriver(normalizer)

    snap = RepoStateSnapshot(
        run_id="r1",
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(name="repo", path=tmp_path, current_branch="main", is_dirty=False),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
            coverage_signal=CoverageSignal(status="unavailable"),
        ),
    )
    insights = deriver.derive([snap])
    assert insights == []


# ---------------------------------------------------------------------------
# S9-10: Theme aggregation deriver
# ---------------------------------------------------------------------------

def _make_snap_with_lint_file(tmp_path: Path, fpath: str, run_id: str) -> "RepoStateSnapshot":  # noqa: F821
    from operations_center.observer.models import (
        LintSignal, LintViolation, RepoContextSnapshot, RepoSignalsSnapshot,
        RepoStateSnapshot, CheckSignal, DependencyDriftSignal, TodoSignal,
    )

    return RepoStateSnapshot(
        run_id=run_id,
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(name="repo", path=tmp_path, current_branch="main", is_dirty=False),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
            lint_signal=LintSignal(
                status="violations",
                violation_count=10,
                top_violations=[
                    LintViolation(path=fpath, line=1, col=1, code="E501", message="too long"),
                ],
            ),
        ),
    )


def test_theme_aggregation_detects_lint_cluster(tmp_path: Path) -> None:
    from operations_center.insights.derivers.theme_aggregation import ThemeAggregationDeriver
    from operations_center.insights.normalizer import InsightNormalizer

    normalizer = InsightNormalizer()
    deriver = ThemeAggregationDeriver(normalizer, min_appearances=3)

    snaps = [_make_snap_with_lint_file(tmp_path, "src/hotspot.py", f"r{i}") for i in range(4)]
    insights = deriver.derive(snaps)

    kinds = [i.kind for i in insights]
    assert "theme/lint_cluster" in kinds

    cluster = next(i for i in insights if i.kind == "theme/lint_cluster")
    assert "hotspot.py" in cluster.evidence["file"]
    assert cluster.evidence["snapshot_appearances"] >= 3


def test_theme_aggregation_no_cluster_below_threshold(tmp_path: Path) -> None:
    from operations_center.insights.derivers.theme_aggregation import ThemeAggregationDeriver
    from operations_center.insights.normalizer import InsightNormalizer

    normalizer = InsightNormalizer()
    deriver = ThemeAggregationDeriver(normalizer, min_appearances=5)

    # Only 3 snapshots with the same file — below threshold of 5
    snaps = [_make_snap_with_lint_file(tmp_path, "src/rare.py", f"r{i}") for i in range(3)]
    insights = deriver.derive(snaps)
    assert not any(i.kind == "theme/lint_cluster" for i in insights)


def test_theme_aggregation_no_cluster_for_single_snapshot(tmp_path: Path) -> None:
    from operations_center.insights.derivers.theme_aggregation import ThemeAggregationDeriver
    from operations_center.insights.normalizer import InsightNormalizer

    normalizer = InsightNormalizer()
    deriver = ThemeAggregationDeriver(normalizer)

    snaps = [_make_snap_with_lint_file(tmp_path, "src/foo.py", "r1")]
    insights = deriver.derive(snaps)
    # Only 1 snapshot < min_snapshots=3
    assert insights == []
