"""Tests for ValidationHistoryCollector pattern detection.

Covers:
- Tasks below 50% failure rate are NOT flagged as repeated patterns.
- Tasks at or above 50% failure rate (with min runs/failures met) ARE flagged.
- ValidationFailureRecord includes the correct failure_rate float value.
- Edge case: exactly 50% failure rate IS flagged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


from operations_center.observer.collectors.validation_history import ValidationHistoryCollector
from operations_center.observer.models import ValidationFailureRecord


@dataclass
class _MinimalSettings:
    report_root: Path


@dataclass
class _MinimalContext:
    settings: _MinimalSettings
    repo_name: str


def _make_run_dir(
    base: Path,
    run_id: str,
    task_id: str,
    repo_key: str,
    outcome_status: str = "executed",
    worker_role: str = "worker",
    validation_passed: bool = True,
) -> Path:
    """Create a fake run directory with the required artifact files."""
    run_dir = base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "control_outcome.json").write_text(
        json.dumps(
            {
                "status": outcome_status,
                "task_id": task_id,
                "worker_role": worker_role,
            }
        )
    )
    (run_dir / "request.json").write_text(
        json.dumps({"task": {"repo_key": repo_key}})
    )
    (run_dir / "validation.json").write_text(
        json.dumps({"passed": validation_passed})
    )
    return run_dir


class TestValidationHistoryPatternDetection:
    """Pattern detection requires min runs, min failures, AND >= 50% failure rate."""

    def test_40_percent_failure_rate_not_flagged(self, tmp_path: Path) -> None:
        """5 runs, 2 failures (40%) — below threshold, status should be nominal."""
        for i in range(5):
            passed = i >= 2  # first 2 fail, last 3 pass
            _make_run_dir(
                tmp_path, f"run-{i}", "task-a", "myrepo",
                validation_passed=passed,
            )

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "nominal"
        assert signal.tasks_with_repeated_failures == []

    def test_100_percent_failure_rate_flagged(self, tmp_path: Path) -> None:
        """2 runs, 2 failures (100%) — above threshold, should be flagged."""
        for i in range(2):
            _make_run_dir(
                tmp_path, f"run-{i}", "task-b", "myrepo",
                validation_passed=False,
            )

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "patterns_detected"
        assert len(signal.tasks_with_repeated_failures) == 1
        rec = signal.tasks_with_repeated_failures[0]
        assert rec.task_id == "task-b"
        assert rec.total_runs == 2
        assert rec.validation_failure_count == 2

    def test_failure_rate_field_value(self, tmp_path: Path) -> None:
        """Verify ValidationFailureRecord carries the correct failure_rate float."""
        for i in range(3):
            _make_run_dir(
                tmp_path, f"run-{i}", "task-c", "myrepo",
                validation_passed=False,
            )

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert len(signal.tasks_with_repeated_failures) == 1
        rec = signal.tasks_with_repeated_failures[0]
        assert rec.failure_rate == 1.0

    def test_50_percent_failure_rate_flagged(self, tmp_path: Path) -> None:
        """4 runs, 2 failures (50%) — exactly at threshold, should be flagged."""
        for i in range(4):
            passed = i >= 2  # first 2 fail, last 2 pass
            _make_run_dir(
                tmp_path, f"run-{i}", "task-d", "myrepo",
                validation_passed=passed,
            )

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "patterns_detected"
        assert len(signal.tasks_with_repeated_failures) == 1
        rec = signal.tasks_with_repeated_failures[0]
        assert rec.task_id == "task-d"
        assert rec.total_runs == 4
        assert rec.validation_failure_count == 2
        assert rec.failure_rate == 0.5

    def test_49_percent_failure_rate_not_flagged(self, tmp_path: Path) -> None:
        """100 runs, 49 failures (49%) — just below threshold, should NOT be flagged."""
        for i in range(100):
            passed = i >= 49  # first 49 fail, last 51 pass
            _make_run_dir(
                tmp_path, f"run-{i:03d}", "task-e", "myrepo",
                validation_passed=passed,
            )

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "nominal"
        assert signal.tasks_with_repeated_failures == []

    def test_1_run_1_failure_not_flagged(self, tmp_path: Path) -> None:
        """1 run, 1 failure — below _MIN_RUNS_FOR_PATTERN=2, should NOT be flagged."""
        _make_run_dir(tmp_path, "run-0", "task-f", "myrepo", validation_passed=False)

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "nominal"
        assert signal.tasks_with_repeated_failures == []

    def test_2_runs_1_failure_not_flagged(self, tmp_path: Path) -> None:
        """2 runs, 1 failure (50% rate but only 1 failure) — below _MIN_FAILURES_FOR_PATTERN=2."""
        _make_run_dir(tmp_path, "run-0", "task-g", "myrepo", validation_passed=False)
        _make_run_dir(tmp_path, "run-1", "task-g", "myrepo", validation_passed=True)

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "nominal"
        assert signal.tasks_with_repeated_failures == []

    def test_50_percent_with_minimum_runs_flagged(self, tmp_path: Path) -> None:
        """2 runs, 1 failure = 50% rate, meets min runs (2) but only 1 failure
        (below _MIN_FAILURES_FOR_PATTERN=2) — should NOT be flagged.
        Tests the conjunction of all 3 conditions."""
        _make_run_dir(tmp_path, "run-0", "task-h", "myrepo", validation_passed=False)
        _make_run_dir(tmp_path, "run-1", "task-h", "myrepo", validation_passed=True)

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "nominal"
        assert signal.tasks_with_repeated_failures == []

    def test_exactly_3_runs_2_failures_flagged(self, tmp_path: Path) -> None:
        """3 runs, 2 failures (66.7%) — all conditions met → flagged."""
        _make_run_dir(tmp_path, "run-0", "task-i", "myrepo", validation_passed=False)
        _make_run_dir(tmp_path, "run-1", "task-i", "myrepo", validation_passed=False)
        _make_run_dir(tmp_path, "run-2", "task-i", "myrepo", validation_passed=True)

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "patterns_detected"
        assert len(signal.tasks_with_repeated_failures) == 1
        rec = signal.tasks_with_repeated_failures[0]
        assert rec.task_id == "task-i"
        assert rec.total_runs == 3
        assert rec.validation_failure_count == 2
        assert rec.failure_rate == 0.667

    def test_failure_rate_rounding_boundary(self, tmp_path: Path) -> None:
        """3 runs, 1 failure = 0.333... — failure_rate should be rounded to 3 decimals (0.333).
        Not flagged because 33.3% < 50% and only 1 failure."""
        _make_run_dir(tmp_path, "run-0", "task-j", "myrepo", validation_passed=False)
        _make_run_dir(tmp_path, "run-1", "task-j", "myrepo", validation_passed=True)
        _make_run_dir(tmp_path, "run-2", "task-j", "myrepo", validation_passed=True)

        ctx = _MinimalContext(settings=_MinimalSettings(report_root=tmp_path), repo_name="myrepo")
        signal = ValidationHistoryCollector().collect(ctx)

        # Not flagged (below threshold), but verify rounding via overall_failure_rate
        assert signal.status == "nominal"
        assert signal.tasks_with_repeated_failures == []
        # overall_failure_rate should be rounded to 3 decimals: 1/3 = 0.333
        assert signal.overall_failure_rate == 0.333


class TestValidationFailureRecordModel:
    def test_failure_rate_default(self) -> None:
        rec = ValidationFailureRecord(
            task_id="t", worker_role="w", total_runs=1, validation_failure_count=0,
        )
        assert rec.failure_rate == 0.0

    def test_failure_rate_round_trip(self) -> None:
        rec = ValidationFailureRecord(
            task_id="t", worker_role="w", total_runs=3,
            validation_failure_count=2, failure_rate=0.667,
        )
        assert rec.failure_rate == 0.667

    def test_failure_rate_boundary_0_499_round_trip(self) -> None:
        """Verify failure_rate=0.499 round-trips correctly (just below 0.5 threshold)."""
        rec = ValidationFailureRecord(
            task_id="t", worker_role="w", total_runs=1000,
            validation_failure_count=499, failure_rate=0.499,
        )
        assert rec.failure_rate == 0.499
