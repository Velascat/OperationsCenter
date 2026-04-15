# tests/spec_director/test_worker_routing.py
from __future__ import annotations
from unittest.mock import MagicMock, patch


def _make_issue(task_kind: str, status: str = "Ready for AI") -> dict:
    return {
        "id": "task-abc",
        "labels": [{"name": f"task-kind: {task_kind}"}],
        "state": {"name": status},
    }


def test_test_role_picks_test_campaign():
    from control_plane.entrypoints.worker.main import ROLE_TASK_KINDS
    assert "test_campaign" in ROLE_TASK_KINDS["test"]


def test_improve_role_picks_improve_campaign():
    from control_plane.entrypoints.worker.main import ROLE_TASK_KINDS
    assert "improve_campaign" in ROLE_TASK_KINDS["improve"]


def test_goal_role_does_not_pick_test_campaign():
    from control_plane.entrypoints.worker.main import ROLE_TASK_KINDS
    assert "test_campaign" not in ROLE_TASK_KINDS["goal"]
