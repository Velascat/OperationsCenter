"""Tests for the circuit-breaker that skips execution when an open fix-validation task exists."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from control_plane.legacy_execution.service import ExecutionService
from control_plane.config.settings import Settings


def _make_plane_client(
    *,
    task_repo: str = "repo_a",
    fix_issues: list[dict] | None = None,
) -> MagicMock:
    """Return a mock PlaneClient with configurable fix-validation issues."""
    pc = MagicMock()
    pc.fetch_issue.return_value = {
        "id": "TASK-10",
        "name": "Implement feature",
        "project_id": "proj",
        "description": f"""## Execution
repo: {task_repo}
base_branch: main
mode: goal

## Goal
Do something.
""",
        "state": {"name": "Ready for AI"},
        "labels": [],
    }
    # Wire to_board_task through a real PlaneClient parser
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


@pytest.fixture(autouse=True)
def _isolate_usage_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect usage store to a temp path so tests don't pollute the real store."""
    usage_path = tmp_path / "usage.json"
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(usage_path))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
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
                "repo_a": {
                    "clone_url": "git@github.com:you/repo_a.git",
                    "default_branch": "main",
                }
            },
            "report_root": str(tmp_path / "reports"),
        }
    )


def test_circuit_breaker_skips_when_open_fix_validation_task(settings: Settings) -> None:
    """run_task returns skipped when an unresolved fix-validation task exists for the repo."""
    fix_issues = [
        {
            "id": "FIX-1",
            "name": "Fix pre-existing validation failure in repo_a",
            "state": {"name": "Ready for AI"},
        }
    ]
    pc = _make_plane_client(fix_issues=fix_issues)
    service = ExecutionService(settings)
    result = service.run_task(pc, "TASK-10")

    assert result.outcome_status == "skipped"
    assert result.outcome_reason == "open_fix_validation_task:FIX-1"
    assert result.success is True
    assert "fix_task=FIX-1" in result.summary
    assert "repo=repo_a" in result.summary


def test_circuit_breaker_does_not_trigger_when_no_fix_task(settings: Settings) -> None:
    """Execution proceeds past the circuit-breaker when no fix-validation task exists."""
    pc = _make_plane_client(fix_issues=[])
    service = ExecutionService(settings)

    # With no fix-validation issues, execution should proceed past the circuit-breaker
    # into the branch_allowed check and beyond. We expect it to reach the repo_setup
    # phase which will fail because git clone isn't available; that proves it passed.
    with pytest.raises(Exception):  # noqa: B017
        service.run_task(pc, "TASK-10")


def test_circuit_breaker_ignores_closed_fix_tasks(settings: Settings) -> None:
    """Done, Blocked, and Cancelled fix-validation tasks do not trigger the circuit-breaker."""
    for state_name in ("Done", "Blocked", "Cancelled"):
        fix_issues = [
            {
                "id": "FIX-2",
                "name": "Fix pre-existing validation failure in repo_a",
                "state": {"name": state_name},
            }
        ]
        pc = _make_plane_client(fix_issues=fix_issues)
        service = ExecutionService(settings)

        # Should proceed past the circuit-breaker (and fail later in the pipeline)
        with pytest.raises(Exception):  # noqa: B017
            service.run_task(pc, "TASK-10")


def test_circuit_breaker_ignores_fix_task_for_other_repo(settings: Settings) -> None:
    """A fix-validation task for a different repo does not trigger the circuit-breaker."""
    fix_issues = [
        {
            "id": "FIX-3",
            "name": "Fix pre-existing validation failure in repo_b",
            "state": {"name": "Ready for AI"},
        }
    ]
    pc = _make_plane_client(fix_issues=fix_issues)
    service = ExecutionService(settings)

    with pytest.raises(Exception):  # noqa: B017
        service.run_task(pc, "TASK-10")
