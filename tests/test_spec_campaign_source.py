"""Verify that spec-campaign-sourced Backlog tasks are promoted by promote_backlog_tasks."""
from __future__ import annotations

from unittest.mock import MagicMock

from control_plane.entrypoints.worker.main import promote_backlog_tasks


def _make_issue(*, task_id: str, state: str, labels: list[str]) -> dict:
    return {
        "id": task_id,
        "name": f"Task {task_id}",
        "state": {"name": state},
        "labels": [{"name": lbl} for lbl in labels],
        "description": "## Execution\nrepo: repo_a\nbase_branch: main\nmode: goal\n",
    }


def test_spec_campaign_source_task_is_promoted():
    issue = _make_issue(
        task_id="task-1",
        state="Backlog",
        labels=["task-kind: goal", "source: spec-campaign", "campaign-id: abc-123", "repo: repo_a"],
    )
    client = MagicMock()
    client.transition_issue.return_value = None
    client.comment_issue.return_value = None

    promoted = promote_backlog_tasks(client, [issue], max_promotions=5)

    assert "task-1" in promoted
    client.transition_issue.assert_called_once_with("task-1", "Ready for AI")
    client.comment_issue.assert_called_once()


def test_unknown_source_task_is_not_promoted():
    """A task with an unrecognized source label is not auto-promoted."""
    issue = _make_issue(
        task_id="task-2",
        state="Backlog",
        labels=["task-kind: goal", "source: some-unknown-source", "repo: repo_a"],
    )
    client = MagicMock()
    promoted = promote_backlog_tasks(client, [issue], max_promotions=5)
    assert promoted == []
    client.transition_issue.assert_not_called()
