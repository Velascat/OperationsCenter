"""End-to-end integration test: validation failures → insight → proposal → circuit-breaker skip.

Exercises the full sequence introduced by stages 1 and 2:
1. Two validation-failing execution runs are recorded for a repo.
2. The ExecutionHealthDeriver (threshold=2) fires persistent_validation_failures.
3. The ExecutionHealthRule emits a proposal candidate from that insight.
4. ExecutionService.run_task skips execution because an open fix-validation task exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from control_plane.application.service import ExecutionService
from control_plane.config.settings import Settings
from control_plane.decision.rules.execution_health import ExecutionHealthRule
from control_plane.insights.derivers.execution_health import ExecutionHealthDeriver
from control_plane.insights.normalizer import InsightNormalizer
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

REPO_KEY = "repo_alpha"


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "plane": {
                "base_url": "http://plane.local",
                "api_token_env": "PLANE_API_TOKEN",
                "workspace_slug": "ws",
                "project_id": "proj",
            },
            "git": {"provider": "github"},
            "kodo": {},
            "repos": {
                REPO_KEY: {
                    "clone_url": f"git@github.com:org/{REPO_KEY}.git",
                    "default_branch": "main",
                }
            },
            "report_root": str(tmp_path / "reports"),
        }
    )


def _make_plane_client(
    *,
    task_repo: str = REPO_KEY,
    fix_issues: list[dict] | None = None,
) -> MagicMock:
    """Return a mock PlaneClient whose task points at *task_repo*."""
    pc = MagicMock()
    pc.fetch_issue.return_value = {
        "id": "TASK-42",
        "name": "Add caching layer",
        "project_id": "proj",
        "description": f"""## Execution
repo: {task_repo}
base_branch: main
mode: goal

## Goal
Add a caching layer for hot paths.
""",
        "state": {"name": "Ready for AI"},
        "labels": [],
    }
    from control_plane.adapters.plane import PlaneClient

    _real = PlaneClient("http://plane.local", "token", "ws", "proj")

    def _to_board_task(issue: dict) -> object:
        try:
            return _real.to_board_task(issue)
        finally:
            _real.close()

    pc.to_board_task.side_effect = _to_board_task
    pc.list_issues.return_value = fix_issues or []
    pc.transition_issue.return_value = None
    pc.comment_issue.return_value = None
    return pc


def _snapshot_with_validation_failures(
    repo: str,
    fail_count: int,
    total_runs: int = 10,
) -> RepoStateSnapshot:
    """Create a RepoStateSnapshot with the given validation failure count."""
    ts = datetime(2026, 4, 5, 12, tzinfo=UTC)
    return RepoStateSnapshot(
        run_id="e2e_test",
        observed_at=ts,
        source_command="test",
        repo=RepoContextSnapshot(
            name=repo,
            path=Path("/tmp/repo"),
            current_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            test_signal=TestSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
            execution_health=ExecutionHealthSignal(
                total_runs=total_runs,
                executed_count=total_runs,
                no_op_count=0,
                validation_failed_count=fail_count,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# End-to-end test
# ---------------------------------------------------------------------------


def test_validation_failures_trigger_insight_proposal_and_circuit_breaker(
    tmp_path: Path,
) -> None:
    """Full pipeline: 2 validation failures → insight → proposal → run_task skip.

    This integration test validates that stages 1 and 2 work together:
    - The lowered threshold (≥2) fires the persistent_validation_failures insight.
    - The decision rule turns it into a proposal candidate.
    - The circuit-breaker in run_task skips execution when a fix task is open.
    """

    # --- Step 1: Simulate 2 validation failures via a snapshot -----------------
    snapshot = _snapshot_with_validation_failures(REPO_KEY, fail_count=2)

    # --- Step 2: Derive insights (threshold=2) ---------------------------------
    deriver = ExecutionHealthDeriver(InsightNormalizer(), validation_failure_threshold=2)
    insights = deriver.derive([snapshot])

    # Must produce exactly the persistent_validation_failures insight
    validation_insights = [
        i for i in insights if "persistent_validation_failures" in i.dedup_key
    ]
    assert len(validation_insights) == 1, (
        f"Expected 1 persistent_validation_failures insight, got {len(validation_insights)}"
    )
    insight = validation_insights[0]
    assert insight.kind == "execution_health"
    assert insight.subject == REPO_KEY
    assert insight.evidence["validation_failed_count"] == 2

    # --- Step 3: Generate proposal candidate from insight ----------------------
    rule = ExecutionHealthRule()
    candidates = rule.evaluate([insight])

    assert len(candidates) == 1, f"Expected 1 candidate, got {len(candidates)}"
    candidate = candidates[0]
    assert candidate.family == "execution_health_followup"
    assert candidate.pattern_key == "persistent_validation_failures"
    assert candidate.subject == REPO_KEY
    assert "validation" in candidate.proposal_outline.title_hint.lower()
    assert "circuit-breaker" in candidate.proposal_outline.summary_hint.lower()

    # --- Step 4: Circuit-breaker skips run_task --------------------------------
    # Simulate that the fix-validation task created by the proposal is now open
    # on the board (as would happen after the proposal is approved).
    fix_issues = [
        {
            "id": "FIX-99",
            "name": f"Fix pre-existing validation failure in {REPO_KEY}",
            "state": {"name": "Ready for AI"},
        }
    ]
    pc = _make_plane_client(fix_issues=fix_issues)
    settings = _settings(tmp_path)
    service = ExecutionService(settings)
    result = service.run_task(pc, "TASK-42")

    assert result.outcome_status == "skipped"
    assert result.outcome_reason == "open_fix_validation_task:FIX-99"
    assert result.success is True
    assert f"repo={REPO_KEY}" in result.summary


def test_below_threshold_no_insight_and_execution_proceeds(tmp_path: Path) -> None:
    """With only 1 validation failure, no insight is derived and execution is not blocked.

    Confirms the threshold boundary: count=1 < threshold=2 means no insight,
    and without a fix-task on the board, run_task proceeds past the circuit-breaker.
    """

    # --- Step 1: Only 1 failure — below threshold ---
    snapshot = _snapshot_with_validation_failures(REPO_KEY, fail_count=1)

    deriver = ExecutionHealthDeriver(InsightNormalizer(), validation_failure_threshold=2)
    insights = deriver.derive([snapshot])
    assert not any("persistent_validation_failures" in i.dedup_key for i in insights)

    # --- Step 2: No fix task on board — circuit-breaker does not fire ---
    pc = _make_plane_client(fix_issues=[])
    settings = _settings(tmp_path)
    service = ExecutionService(settings)

    # Execution should proceed past the circuit-breaker and fail deeper in the
    # pipeline (e.g. during git clone), proving the breaker didn't block it.
    with pytest.raises(Exception):  # noqa: B017
        service.run_task(pc, "TASK-42")
