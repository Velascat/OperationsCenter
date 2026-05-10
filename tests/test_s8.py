# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for S8 autonomy gaps.

Coverage:
  S8-2:  ExecutionOutcomeDeriver — Phase 4 execution feedback depth
  S8-5:  Rollback on post-merge regression (GitClient.revert_commit)
  S8-7:  Quality trend tracking (QualityTrendDeriver lint/type delta detection)
  S8-8:  Runtime error ingestion (webhook receiver creates tasks; dedup logic)
  S8-10: Confidence calibration infrastructure (ConfidenceCalibrationStore)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# S8-2: ExecutionOutcomeDeriver (Phase 4)
# ---------------------------------------------------------------------------

from operations_center.insights.derivers.execution_outcome import ExecutionOutcomeDeriver  # noqa: E402
from operations_center.insights.normalizer import InsightNormalizer  # noqa: E402
from operations_center.observer.models import (  # noqa: E402
    RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot,
    CheckSignal, DependencyDriftSignal, TodoSignal,
)


def _make_snapshot(repo_name: str = "testrepo") -> RepoStateSnapshot:
    return RepoStateSnapshot(
        run_id="r1",
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(name=repo_name, path=Path("/tmp"), current_branch="main", is_dirty=False),
        signals=RepoSignalsSnapshot(
            test_signal=CheckSignal(status="unavailable"),
            dependency_drift=DependencyDriftSignal(status="unavailable"),
            todo_signal=TodoSignal(),
        ),
    )


def test_execution_outcome_deriver_empty_when_no_artifacts(tmp_path: Path) -> None:
    normalizer = InsightNormalizer()
    deriver = ExecutionOutcomeDeriver(normalizer, artifact_root=tmp_path / "nonexistent")
    insights = deriver.derive([_make_snapshot()])
    assert insights == []


def test_execution_outcome_deriver_detects_timeout_pattern(tmp_path: Path) -> None:
    """Two timeout failures → execution_outcome/timeout_pattern insight."""
    normalizer = InsightNormalizer()
    artifact_root = tmp_path / "kodo_plane"
    artifact_root.mkdir()

    for i in range(2):
        run_dir = artifact_root / f"2025-01-01_task{i:04d}_r{i}"
        run_dir.mkdir()
        (run_dir / "request.json").write_text(json.dumps({"task": {"repo_key": "myrepo"}}))
        (run_dir / "control_outcome.json").write_text(json.dumps({
            "outcome_status": "blocked",
            "blocked_classification": "timeout",
        }))

    deriver = ExecutionOutcomeDeriver(normalizer, artifact_root=artifact_root)
    insights = deriver.derive([_make_snapshot("myrepo")])
    kinds = [i.kind for i in insights]
    assert "execution_outcome/timeout_pattern" in kinds


def test_execution_outcome_deriver_detects_validation_loop(tmp_path: Path) -> None:
    """Same task failing validation 3+ times → execution_outcome/validation_loop insight."""
    normalizer = InsightNormalizer()
    artifact_root = tmp_path / "kodo_plane"
    artifact_root.mkdir()
    task_id = "looptask"

    for i in range(3):
        run_dir = artifact_root / f"run_{i:04d}"
        run_dir.mkdir()
        (run_dir / "request.json").write_text(json.dumps({"task": {"repo_key": "repo1", "task_id": task_id}}))
        (run_dir / "control_outcome.json").write_text(json.dumps({
            "outcome_status": "blocked",
            "blocked_classification": "validation_failure",
        }))

    deriver = ExecutionOutcomeDeriver(normalizer, artifact_root=artifact_root)
    insights = deriver.derive([_make_snapshot("repo1")])
    kinds = [i.kind for i in insights]
    assert "execution_outcome/validation_loop" in kinds



# ---------------------------------------------------------------------------
# S8-5: Rollback on post-merge regression
# ---------------------------------------------------------------------------

from operations_center.adapters.git.client import GitClient  # noqa: E402


def test_git_revert_commit_creates_branch(tmp_path: Path) -> None:
    """GitClient.revert_commit() creates a new branch and applies the revert commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    import subprocess
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True, capture_output=True)
    (repo_path / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, check=True, capture_output=True)
    (repo_path / "file.txt").write_text("changed")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo_path, check=True, capture_output=True)

    sha_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True)
    merge_sha = sha_result.stdout.strip()

    gc = GitClient()
    success = gc.revert_commit(repo_path, merge_sha, new_branch="revert/test")
    assert success is True

    # Should be on the revert branch
    branch_result = subprocess.run(["git", "branch", "--show-current"], cwd=repo_path, capture_output=True, text=True)
    assert branch_result.stdout.strip() == "revert/test"

    # File should be back to initial
    assert (repo_path / "file.txt").read_text() == "initial"




# ---------------------------------------------------------------------------
# S8-7: Quality trend tracking
# ---------------------------------------------------------------------------

from operations_center.insights.derivers.quality_trend import QualityTrendDeriver  # noqa: E402
from operations_center.observer.models import LintSignal, TypeSignal  # noqa: E402


def _make_snapshot_with_metrics(
    lint_violations: int, type_errors: int, days_ago: int = 0
) -> RepoStateSnapshot:
    from operations_center.observer.models import RepoSignalsSnapshot
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unavailable"),
        dependency_drift=DependencyDriftSignal(status="unavailable"),
        todo_signal=TodoSignal(),
        lint_signal=LintSignal(
            status="violations" if lint_violations > 0 else "clean",
            violation_count=lint_violations,
        ),
        type_signal=TypeSignal(
            status="errors" if type_errors > 0 else "clean",
            error_count=type_errors,
        ),
    )
    snap = RepoStateSnapshot(
        run_id=f"r-{days_ago}",
        observed_at=datetime.now(UTC) - timedelta(days=days_ago),
        source_command="test",
        repo=RepoContextSnapshot(name="repo", path=Path("/tmp"), current_branch="main", is_dirty=False),
        signals=signals,
    )
    return snap


def test_quality_trend_detects_lint_improving() -> None:
    """Lint violations dropping consistently → quality_trend/lint_improving."""
    normalizer = InsightNormalizer()
    deriver = QualityTrendDeriver(normalizer)
    # newest → oldest order (as derivers receive snapshots)
    snapshots = [
        _make_snapshot_with_metrics(lint_violations=5, type_errors=0, days_ago=0),
        _make_snapshot_with_metrics(lint_violations=8, type_errors=0, days_ago=1),
        _make_snapshot_with_metrics(lint_violations=12, type_errors=0, days_ago=2),
    ]
    insights = deriver.derive(snapshots)
    kinds = [i.kind for i in insights]
    assert "quality_trend/lint_improving" in kinds


def test_quality_trend_detects_lint_degrading() -> None:
    """Lint violations growing → quality_trend/lint_degrading."""
    normalizer = InsightNormalizer()
    deriver = QualityTrendDeriver(normalizer)
    snapshots = [
        _make_snapshot_with_metrics(lint_violations=20, type_errors=0, days_ago=0),
        _make_snapshot_with_metrics(lint_violations=12, type_errors=0, days_ago=1),
        _make_snapshot_with_metrics(lint_violations=5, type_errors=0, days_ago=2),
    ]
    insights = deriver.derive(snapshots)
    kinds = [i.kind for i in insights]
    assert "quality_trend/lint_degrading" in kinds


def test_quality_trend_no_insight_for_single_snapshot() -> None:
    """Need at least 3 snapshots to detect a trend."""
    normalizer = InsightNormalizer()
    deriver = QualityTrendDeriver(normalizer)
    insights = deriver.derive([_make_snapshot_with_metrics(10, 5)])
    assert insights == []


def test_quality_trend_stagnant() -> None:
    """Metrics available but no significant change → quality_trend/stagnant."""
    normalizer = InsightNormalizer()
    deriver = QualityTrendDeriver(normalizer)
    snapshots = [
        _make_snapshot_with_metrics(lint_violations=10, type_errors=5, days_ago=0),
        _make_snapshot_with_metrics(lint_violations=10, type_errors=5, days_ago=1),
        _make_snapshot_with_metrics(lint_violations=10, type_errors=5, days_ago=2),
    ]
    insights = deriver.derive(snapshots)
    kinds = [i.kind for i in insights]
    assert "quality_trend/stagnant" in kinds


# ---------------------------------------------------------------------------
# S8-8: Runtime error ingestion
# ---------------------------------------------------------------------------

from operations_center.entrypoints.error_ingest.main import _is_duplicate, _mark_created, _dedup_key  # noqa: E402


def test_error_ingest_dedup_key_is_stable() -> None:
    k1 = _dedup_key("myrepo", "NullPointerException in PaymentService")
    k2 = _dedup_key("myrepo", "NullPointerException in PaymentService")
    assert k1 == k2


def test_error_ingest_dedup_not_duplicate_before_mark(tmp_path: Path) -> None:
    import operations_center.entrypoints.error_ingest.main as eingest
    orig = eingest._DEDUP_STATE_PATH
    eingest._DEDUP_STATE_PATH = tmp_path / "dedup.json"

    key = _dedup_key("repo", "some error")
    assert not _is_duplicate(key, window_seconds=3600)

    eingest._DEDUP_STATE_PATH = orig


def test_error_ingest_dedup_is_duplicate_after_mark(tmp_path: Path) -> None:
    import operations_center.entrypoints.error_ingest.main as eingest
    orig = eingest._DEDUP_STATE_PATH
    eingest._DEDUP_STATE_PATH = tmp_path / "dedup.json"

    key = _dedup_key("repo", "repeated error")
    _mark_created(key)
    assert _is_duplicate(key, window_seconds=3600)

    eingest._DEDUP_STATE_PATH = orig


def test_error_ingest_webhook_creates_plane_task(tmp_path: Path) -> None:
    """The webhook handler creates a Plane task for a valid ingest POST."""
    import operations_center.entrypoints.error_ingest.main as eingest
    orig = eingest._DEDUP_STATE_PATH
    eingest._DEDUP_STATE_PATH = tmp_path / "dedup.json"

    created_tasks = []

    class _MockClient:
        def create_issue(self, name, **_):
            t = {"id": f"task-{len(created_tasks)}", "name": name}
            created_tasks.append(t)
            return t

    from operations_center.entrypoints.error_ingest.main import _make_webhook_handler
    handler_class = _make_webhook_handler(_MockClient(), "myrepo")

    # Simulate a POST request using a mock socket
    payload = json.dumps({
        "title": "Database connection timeout",
        "severity": "error",
        "source": "test",
    }).encode()

    req = MagicMock()
    req.headers = {"Content-Length": str(len(payload))}
    req.rfile = MagicMock()
    req.rfile.read.return_value = payload

    responses = []
    req.wfile = MagicMock()
    req.wfile.write.side_effect = responses.append
    req.send_response = MagicMock()
    req.end_headers = MagicMock()
    req.path = "/ingest"

    handler = handler_class.__new__(handler_class)
    handler.__dict__.update(req.__dict__)

    with patch.object(handler_class, "__init__", lambda *a, **k: None):
        handler.do_POST()

    assert len(created_tasks) > 0
    assert "Database connection timeout" in created_tasks[0]["name"]

    eingest._DEDUP_STATE_PATH = orig



# ---------------------------------------------------------------------------
# S8-10: Confidence calibration infrastructure
# ---------------------------------------------------------------------------

from operations_center.tuning.calibration import ConfidenceCalibrationStore, _MIN_SAMPLE_SIZE  # noqa: E402


def test_calibration_record_and_retrieve(tmp_path: Path) -> None:
    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    store.record("lint_fix", "high", "merged")
    store.record("lint_fix", "high", "abandoned")
    rate = store.calibration_for("lint_fix", "high")
    assert rate is None  # below MIN_SAMPLE_SIZE


def test_calibration_report_after_enough_records(tmp_path: Path) -> None:
    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("type_fix", "medium", "merged")
    records = store.report()
    assert len(records) == 1
    r = records[0]
    assert r.family == "type_fix"
    assert r.confidence == "medium"
    assert r.total == _MIN_SAMPLE_SIZE
    assert r.acceptance_rate == pytest.approx(1.0)
    assert r.expected_rate == 0.5
    assert r.calibration_ratio == pytest.approx(2.0)


def test_calibration_ignores_unknown_confidence(tmp_path: Path) -> None:
    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    store.record("lint_fix", "extreme", "merged")  # not a valid confidence label
    assert store.calibration_for("lint_fix", "extreme") is None
    assert store.report() == []


def test_calibration_detects_over_confident_family(tmp_path: Path) -> None:
    """A family with high confidence but low actual acceptance has calibration_ratio < 1.0."""
    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("arch_promotion", "high", "abandoned")  # 0% acceptance
    records = store.report()
    assert len(records) == 1
    r = records[0]
    assert r.acceptance_rate == pytest.approx(0.0)
    assert r.calibration_ratio == pytest.approx(0.0)
