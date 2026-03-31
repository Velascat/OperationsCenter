from pathlib import Path

import pytest

from control_plane.application.service import ExecutionService
from control_plane.config.settings import Settings


class DummyPlaneClient:
    def fetch_issue(self, task_id: str) -> dict[str, object]:
        return {
            "id": task_id,
            "name": "Task",
            "project_id": "proj",
            "description": """## Execution
repo: repo_a
base_branch: main
mode: test

## Goal
Do thing.
""",
            "state": {"name": "Ready for AI"},
            "labels": [],
        }

    def to_board_task(self, issue: dict[str, object]):  # type: ignore[no-untyped-def]
        from control_plane.adapters.plane import PlaneClient

        c = PlaneClient("http://plane.local", "token", "ws", "proj")
        try:
            return c.to_board_task(issue)
        finally:
            c.close()

    def transition_issue(self, task_id: str, state: str) -> None:  # noqa: ARG002
        return

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:  # noqa: ARG002
        return


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


def test_unsupported_execution_mode_fails_cleanly(settings: Settings) -> None:
    service = ExecutionService(settings)
    with pytest.raises(ValueError, match="supports only 'goal'"):
        service.run_task(DummyPlaneClient(), "TASK-1")
