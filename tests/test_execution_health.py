from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from control_plane.decision.rules.execution_health import ExecutionHealthRule
from control_plane.insights.derivers.execution_health import ExecutionHealthDeriver
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.collectors.execution_health import ExecutionArtifactCollector
from control_plane.observer.models import (
    DependencyDriftSignal,
    ExecutionHealthSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TestSignal,
    TodoSignal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_run(
    root: Path,
    *,
    repo_key: str,
    run_id: str,
    task_id: str,
    worker_role: str,
    outcome_status: str,
    outcome_reason: str | None = None,
    validation_passed: bool | None = None,
) -> None:
    run_dir = root / f"20260404T120000Z_{task_id}_{run_id}"
    run_dir.mkdir(parents=True)
    (run_dir / "control_outcome.json").write_text(
        json.dumps(
            {
                "action": "execute_task",
                "status": outcome_status,
                "reason": outcome_reason,
                "task_id": task_id,
                "worker_role": worker_role,
            }
        )
    )
    (run_dir / "request.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "task": {"task_id": task_id, "repo_key": repo_key},
                "repo_target": {"repo_key": repo_key},
            }
        )
    )
    if validation_passed is not None:
        (run_dir / "validation.json").write_text(
            json.dumps({"passed": validation_passed, "results": []})
        )


def _make_snapshot(
    repo_name: str,
    execution_health: ExecutionHealthSignal,
    observed_at: datetime | None = None,
) -> RepoStateSnapshot:
    ts = observed_at or datetime(2026, 4, 4, 12, tzinfo=UTC)
    return RepoStateSnapshot(
        run_id="obs_test",
        observed_at=ts,
        source_command="test",
        repo=RepoContextSnapshot(
            name=repo_name,
            path=Path("/tmp/repo"),
            current_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            test_signal=TestSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
            execution_health=execution_health,
        ),
    )


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class _FakeSettings:
    def __init__(self, report_root: Path) -> None:
        self.report_root = report_root


class _FakeContext:
    def __init__(self, repo_name: str, report_root: Path) -> None:
        self.repo_name = repo_name
        self.settings = _FakeSettings(report_root)


def test_collector_counts_outcomes_for_matching_repo(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    # 3 no_ops for the target repo
    for i in range(3):
        _write_run(root, repo_key="ControlPlane", run_id=f"r{i}", task_id=f"t{i}", worker_role="goal", outcome_status="no_op", outcome_reason="no_material_change")
    # 2 executed
    _write_run(root, repo_key="ControlPlane", run_id="r3", task_id="t3", worker_role="goal", outcome_status="executed")
    _write_run(root, repo_key="ControlPlane", run_id="r4", task_id="t4", worker_role="test", outcome_status="executed")
    # 1 run for a different repo — should be ignored
    _write_run(root, repo_key="OtherRepo", run_id="r5", task_id="t5", worker_role="goal", outcome_status="no_op")

    ctx = _FakeContext("ControlPlane", root)
    sig = ExecutionArtifactCollector().collect(ctx)  # type: ignore[arg-type]

    assert sig.total_runs == 5
    assert sig.no_op_count == 3
    assert sig.executed_count == 2
    assert sig.validation_failed_count == 0
    assert len(sig.recent_runs) == 5


def test_collector_counts_validation_failures(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    _write_run(root, repo_key="ControlPlane", run_id="r0", task_id="t0", worker_role="goal", outcome_status="executed", validation_passed=False)
    _write_run(root, repo_key="ControlPlane", run_id="r1", task_id="t1", worker_role="goal", outcome_status="executed", validation_passed=False)
    _write_run(root, repo_key="ControlPlane", run_id="r2", task_id="t2", worker_role="goal", outcome_status="executed", validation_passed=True)

    ctx = _FakeContext("ControlPlane", root)
    sig = ExecutionArtifactCollector().collect(ctx)  # type: ignore[arg-type]

    assert sig.validation_failed_count == 2
    assert sig.executed_count == 3


def test_collector_returns_empty_signal_when_no_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    ctx = _FakeContext("ControlPlane", root)
    sig = ExecutionArtifactCollector().collect(ctx)  # type: ignore[arg-type]

    assert sig.total_runs == 0
    assert sig.recent_runs == []


def test_collector_counts_unknown_outcomes(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    _write_run(root, repo_key="ControlPlane", run_id="r0", task_id="t0", worker_role="goal", outcome_status="unknown")
    _write_run(root, repo_key="ControlPlane", run_id="r1", task_id="t1", worker_role="goal", outcome_status="unknown")
    _write_run(root, repo_key="ControlPlane", run_id="r2", task_id="t2", worker_role="goal", outcome_status="executed")

    ctx = _FakeContext("ControlPlane", root)
    sig = ExecutionArtifactCollector().collect(ctx)  # type: ignore[arg-type]

    assert sig.total_runs == 3
    assert sig.unknown_count == 2
    assert sig.executed_count == 1
    assert sig.error_count == 0


def test_collector_counts_error_outcomes(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    _write_run(root, repo_key="ControlPlane", run_id="r0", task_id="t0", worker_role="goal", outcome_status="error")
    _write_run(root, repo_key="ControlPlane", run_id="r1", task_id="t1", worker_role="goal", outcome_status="error")
    _write_run(root, repo_key="ControlPlane", run_id="r2", task_id="t2", worker_role="goal", outcome_status="error")
    _write_run(root, repo_key="ControlPlane", run_id="r3", task_id="t3", worker_role="goal", outcome_status="executed")

    ctx = _FakeContext("ControlPlane", root)
    sig = ExecutionArtifactCollector().collect(ctx)  # type: ignore[arg-type]

    assert sig.total_runs == 4
    assert sig.error_count == 3
    assert sig.executed_count == 1
    assert sig.unknown_count == 0


def test_collector_counts_mixed_unknown_and_error_outcomes(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    _write_run(root, repo_key="ControlPlane", run_id="r0", task_id="t0", worker_role="goal", outcome_status="unknown")
    _write_run(root, repo_key="ControlPlane", run_id="r1", task_id="t1", worker_role="goal", outcome_status="error")
    _write_run(root, repo_key="ControlPlane", run_id="r2", task_id="t2", worker_role="goal", outcome_status="no_op")
    _write_run(root, repo_key="ControlPlane", run_id="r3", task_id="t3", worker_role="goal", outcome_status="executed")
    _write_run(root, repo_key="ControlPlane", run_id="r4", task_id="t4", worker_role="goal", outcome_status="unknown")

    ctx = _FakeContext("ControlPlane", root)
    sig = ExecutionArtifactCollector().collect(ctx)  # type: ignore[arg-type]

    assert sig.total_runs == 5
    assert sig.unknown_count == 2
    assert sig.error_count == 1
    assert sig.no_op_count == 1
    assert sig.executed_count == 1


def test_collector_ignores_dirs_without_required_files(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    (root / "incomplete_run").mkdir()
    (root / "incomplete_run" / "control_outcome.json").write_text('{"status":"no_op"}')
    # no request.json — should be skipped

    ctx = _FakeContext("ControlPlane", root)
    sig = ExecutionArtifactCollector().collect(ctx)  # type: ignore[arg-type]

    assert sig.total_runs == 0


# ---------------------------------------------------------------------------
# Deriver
# ---------------------------------------------------------------------------


def test_deriver_emits_high_no_op_rate_insight(tmp_path: Path) -> None:
    # 6 runs, 4 no_ops → 67% no_op rate ≥ 50% threshold
    sig = ExecutionHealthSignal(total_runs=6, executed_count=2, no_op_count=4, validation_failed_count=0)
    snapshot = _make_snapshot("ControlPlane", sig)

    insights = ExecutionHealthDeriver(InsightNormalizer()).derive([snapshot])

    assert any(i.kind == "execution_health" and "high_no_op_rate" in i.dedup_key for i in insights)
    high_nop = next(i for i in insights if "high_no_op_rate" in i.dedup_key)
    assert high_nop.evidence["no_op_rate"] == pytest.approx(0.67, abs=0.01)


def test_deriver_does_not_emit_when_below_min_runs(tmp_path: Path) -> None:
    # Only 4 runs — below the 5-run minimum
    sig = ExecutionHealthSignal(total_runs=4, executed_count=0, no_op_count=4, validation_failed_count=0)
    snapshot = _make_snapshot("ControlPlane", sig)

    insights = ExecutionHealthDeriver(InsightNormalizer()).derive([snapshot])

    assert not any("high_no_op_rate" in i.dedup_key for i in insights)


def test_deriver_does_not_emit_when_no_op_rate_below_threshold(tmp_path: Path) -> None:
    # 10 runs, 3 no_ops → 30% < 50% threshold
    sig = ExecutionHealthSignal(total_runs=10, executed_count=7, no_op_count=3, validation_failed_count=0)
    snapshot = _make_snapshot("ControlPlane", sig)

    insights = ExecutionHealthDeriver(InsightNormalizer()).derive([snapshot])

    assert not any("high_no_op_rate" in i.dedup_key for i in insights)


def test_deriver_emits_persistent_validation_failures_insight(tmp_path: Path) -> None:
    sig = ExecutionHealthSignal(total_runs=10, executed_count=5, no_op_count=5, validation_failed_count=2)
    snapshot = _make_snapshot("ControlPlane", sig)

    insights = ExecutionHealthDeriver(InsightNormalizer()).derive([snapshot])

    assert any("persistent_validation_failures" in i.dedup_key for i in insights)


def test_deriver_does_not_emit_validation_insight_below_threshold(tmp_path: Path) -> None:
    sig = ExecutionHealthSignal(total_runs=10, executed_count=5, no_op_count=5, validation_failed_count=1)
    snapshot = _make_snapshot("ControlPlane", sig)

    insights = ExecutionHealthDeriver(InsightNormalizer()).derive([snapshot])

    assert not any("persistent_validation_failures" in i.dedup_key for i in insights)


def test_deriver_respects_custom_threshold(tmp_path: Path) -> None:
    deriver = ExecutionHealthDeriver(InsightNormalizer(), validation_failure_threshold=4)

    # count=3 should NOT trigger with threshold=4
    sig_below = ExecutionHealthSignal(total_runs=10, executed_count=5, no_op_count=5, validation_failed_count=3)
    snapshot_below = _make_snapshot("ControlPlane", sig_below)
    insights_below = deriver.derive([snapshot_below])
    assert not any("persistent_validation_failures" in i.dedup_key for i in insights_below)

    # count=4 SHOULD trigger with threshold=4
    sig_at = ExecutionHealthSignal(total_runs=10, executed_count=5, no_op_count=5, validation_failed_count=4)
    snapshot_at = _make_snapshot("ControlPlane", sig_at)
    insights_at = deriver.derive([snapshot_at])
    assert any("persistent_validation_failures" in i.dedup_key for i in insights_at)


def test_deriver_emits_repeated_unknown_failures_at_threshold() -> None:
    # unknown_count=1 + error_count=1 = 2 >= default threshold of 2
    sig = ExecutionHealthSignal(total_runs=5, executed_count=3, no_op_count=0, unknown_count=1, error_count=1)
    snapshot = _make_snapshot("ControlPlane", sig)

    insights = ExecutionHealthDeriver(InsightNormalizer()).derive([snapshot])

    assert any("repeated_unknown_failures" in i.dedup_key for i in insights)
    matched = next(i for i in insights if "repeated_unknown_failures" in i.dedup_key)
    assert matched.evidence["unknown_count"] == 1
    assert matched.evidence["error_count"] == 1
    assert matched.evidence["unknown_error_total"] == 2
    assert matched.evidence["pattern"] == "repeated_unknown_failures"


def test_deriver_no_repeated_unknown_failures_below_threshold() -> None:
    # unknown_count=1 + error_count=0 = 1 < default threshold of 2
    sig = ExecutionHealthSignal(total_runs=5, executed_count=4, no_op_count=0, unknown_count=1, error_count=0)
    snapshot = _make_snapshot("ControlPlane", sig)

    insights = ExecutionHealthDeriver(InsightNormalizer()).derive([snapshot])

    assert not any("repeated_unknown_failures" in i.dedup_key for i in insights)


def test_deriver_repeated_unknown_failures_respects_custom_threshold() -> None:
    deriver = ExecutionHealthDeriver(InsightNormalizer(), unknown_failure_threshold=4)

    # 3 < 4 threshold → no insight
    sig_below = ExecutionHealthSignal(total_runs=5, executed_count=2, no_op_count=0, unknown_count=2, error_count=1)
    snapshot_below = _make_snapshot("ControlPlane", sig_below)
    insights_below = deriver.derive([snapshot_below])
    assert not any("repeated_unknown_failures" in i.dedup_key for i in insights_below)

    # 4 >= 4 threshold → insight
    sig_at = ExecutionHealthSignal(total_runs=6, executed_count=2, no_op_count=0, unknown_count=2, error_count=2)
    snapshot_at = _make_snapshot("ControlPlane", sig_at)
    insights_at = deriver.derive([snapshot_at])
    assert any("repeated_unknown_failures" in i.dedup_key for i in insights_at)


def test_deriver_returns_empty_for_empty_snapshots() -> None:
    assert ExecutionHealthDeriver(InsightNormalizer()).derive([]) == []


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------


def _make_insight(pattern: str, repo: str = "ControlPlane") -> object:
    from control_plane.insights.models import DerivedInsight

    ts = datetime(2026, 4, 4, 12, tzinfo=UTC)
    return DerivedInsight(
        insight_id=f"execution_health:{repo}:{pattern}",
        dedup_key=f"execution_health|{repo}|{pattern}",
        kind="execution_health",
        subject=repo,
        status="present",
        evidence={"repo": repo, "pattern": pattern, "total_runs": 10, "no_op_count": 6, "no_op_rate": 0.6, "validation_failed_count": 3, "executed_count": 5},
        first_seen_at=ts,
        last_seen_at=ts,
    )


def test_rule_produces_candidate_for_high_no_op_rate() -> None:
    specs = ExecutionHealthRule().evaluate([_make_insight("high_no_op_rate")])  # type: ignore[list-item]

    assert len(specs) == 1
    assert specs[0].family == "execution_health_followup"
    assert specs[0].pattern_key == "high_no_op_rate"
    assert "60%" in specs[0].proposal_outline.title_hint


def test_rule_produces_candidate_for_persistent_validation_failures() -> None:
    specs = ExecutionHealthRule().evaluate([_make_insight("persistent_validation_failures")])  # type: ignore[list-item]

    assert len(specs) == 1
    assert specs[0].family == "execution_health_followup"
    assert specs[0].pattern_key == "persistent_validation_failures"
    assert "validation" in specs[0].proposal_outline.title_hint.lower()


def test_rule_ignores_non_execution_health_insights() -> None:
    from control_plane.insights.models import DerivedInsight

    ts = datetime(2026, 4, 4, 12, tzinfo=UTC)
    other = DerivedInsight(
        insight_id="observation_coverage:test_signal:persistent_unavailable",
        dedup_key="observation_coverage|test_signal|persistent_unavailable",
        kind="observation_coverage",
        subject="test_signal",
        status="present",
        evidence={},
        first_seen_at=ts,
        last_seen_at=ts,
    )

    specs = ExecutionHealthRule().evaluate([other])

    assert specs == []


def test_rule_produces_candidates_for_both_patterns() -> None:
    insights = [_make_insight("high_no_op_rate"), _make_insight("persistent_validation_failures")]
    specs = ExecutionHealthRule().evaluate(insights)  # type: ignore[arg-type]

    assert len(specs) == 2
    families = {s.family for s in specs}
    assert families == {"execution_health_followup"}
    patterns = {s.pattern_key for s in specs}
    assert patterns == {"high_no_op_rate", "persistent_validation_failures"}


# ---------------------------------------------------------------------------
# ExecutionService circuit-breaker: repeated unknown/error failures
# ---------------------------------------------------------------------------


def _make_service_with_report_root(report_root: Path):
    """Create an ExecutionService with only settings.report_root wired up."""
    from unittest.mock import MagicMock, patch

    from control_plane.application.service import ExecutionService

    mock_settings = MagicMock()
    mock_settings.report_root = report_root

    with patch.object(ExecutionService, "__init__", lambda self, *a, **kw: None):
        svc = ExecutionService.__new__(ExecutionService)
    svc.settings = mock_settings
    svc.logger = logging.getLogger("test")
    return svc


def test_service_skips_on_repeated_unknown_failures(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    # 2 unknown/error outcomes — meets default threshold of 2
    _write_run(root, repo_key="ControlPlane", run_id="r0", task_id="t0", worker_role="goal", outcome_status="unknown")
    _write_run(root, repo_key="ControlPlane", run_id="r1", task_id="t1", worker_role="goal", outcome_status="error")
    _write_run(root, repo_key="ControlPlane", run_id="r2", task_id="t2", worker_role="goal", outcome_status="executed")

    svc = _make_service_with_report_root(root)

    assert svc._check_repeated_unknown_failures("ControlPlane") is True


def test_service_does_not_skip_below_unknown_threshold(tmp_path: Path) -> None:
    root = tmp_path / "kodo_plane"
    root.mkdir()
    # Only 1 unknown outcome — below default threshold of 2
    _write_run(root, repo_key="ControlPlane", run_id="r0", task_id="t0", worker_role="goal", outcome_status="unknown")
    _write_run(root, repo_key="ControlPlane", run_id="r1", task_id="t1", worker_role="goal", outcome_status="executed")
    _write_run(root, repo_key="ControlPlane", run_id="r2", task_id="t2", worker_role="goal", outcome_status="no_op")

    svc = _make_service_with_report_root(root)

    assert svc._check_repeated_unknown_failures("ControlPlane") is False


def test_service_skip_produces_correct_result_fields(tmp_path: Path) -> None:
    """When the unknown-failures circuit-breaker fires during execute(),
    the returned ExecutionResult must have the expected fields."""
    from unittest.mock import MagicMock, patch

    from control_plane.application.service import ExecutionService
    from control_plane.domain import BoardTask, ExecutionResult

    root = tmp_path / "kodo_plane"
    root.mkdir()
    # Write 2 unknown runs so the breaker triggers
    _write_run(root, repo_key="MyRepo", run_id="r0", task_id="t0", worker_role="goal", outcome_status="unknown")
    _write_run(root, repo_key="MyRepo", run_id="r1", task_id="t1", worker_role="goal", outcome_status="error")

    # Build a minimal service with mocked internals
    with patch.object(ExecutionService, "__init__", lambda self, *a, **kw: None):
        svc = ExecutionService.__new__(ExecutionService)

    mock_settings = MagicMock()
    mock_settings.report_root = root
    svc.settings = mock_settings
    svc.logger = logging.getLogger("test")

    # Mock reporter
    mock_reporter = MagicMock()
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()
    mock_reporter.create_run_dir.return_value = run_dir
    mock_reporter.write_request_context.return_value = "artifact0"
    mock_reporter.write_control_outcome.return_value = "artifact1"
    mock_reporter.write_summary.return_value = "artifact2"
    svc.reporter = mock_reporter

    # Mock workspace
    mock_workspace = MagicMock()
    mock_workspace.create.return_value = tmp_path / "ws"
    svc.workspace = mock_workspace

    # Mock usage_store — need noop_decision, budget_decision
    mock_usage = MagicMock()
    noop_decision = MagicMock()
    noop_decision.should_skip = False
    mock_usage.noop_decision.return_value = noop_decision
    mock_usage.issue_signature.return_value = "sig"
    budget_decision = MagicMock()
    budget_decision.allowed = True
    mock_usage.budget_decision.return_value = budget_decision
    retry_decision = MagicMock()
    retry_decision.allowed = True
    mock_usage.retry_decision.return_value = retry_decision
    svc.usage_store = mock_usage

    # Mock plane_client
    mock_plane = MagicMock()
    task = BoardTask(
        task_id="TSK-1",
        project_id="proj-1",
        title="Test task",
        description="desc",
        status="Todo",
        repo_key="MyRepo",
        base_branch="main",
        execution_mode="goal",
        goal_text="do something",
    )
    mock_plane.fetch_issue.return_value = {"id": "TSK-1"}
    mock_plane.to_board_task.return_value = task

    # _validate_task_contract and _repo_target_for
    svc._validate_task_contract = MagicMock()  # type: ignore[method-assign]
    mock_repo_target = MagicMock()
    mock_repo_target.repo_key = "MyRepo"
    mock_repo_target.allowed_base_branches = None
    svc._repo_target_for = MagicMock(return_value=mock_repo_target)  # type: ignore[method-assign]
    svc._find_open_fix_validation_task = MagicMock(return_value=None)  # type: ignore[method-assign]

    result = svc.run_task(mock_plane, "TSK-1", worker_role="goal")

    assert isinstance(result, ExecutionResult)
    assert result.outcome_status == "skipped"
    assert result.outcome_reason == "repeated_unknown_failures"
    assert result.success is True
