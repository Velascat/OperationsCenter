import json
import logging
from pathlib import Path

import pytest

from control_plane.application.service import ExecutionService
from control_plane.config.settings import Settings


class FailingParsePlaneClient:
    def fetch_issue(self, task_id: str) -> dict[str, object]:
        return {
            "id": task_id,
            "name": "Task",
            "project_id": "proj",
            "description": """## Execution
repo: repo_a
base_branch: main
mode: bad

## Goal
Do thing.
""",
            "state": {"name": "Ready for AI"},
            "labels": [],
        }

    def to_board_task(self, issue: dict[str, object]):  # type: ignore[no-untyped-def]
        from control_plane.adapters.plane import PlaneClient

        client = PlaneClient("http://plane.local", "token", "ws", "proj")
        try:
            return client.to_board_task(issue)
        finally:
            client.close()

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


def test_structured_logging_includes_run_id_and_phase(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    service = ExecutionService(settings)

    with caplog.at_level(logging.INFO):
        with pytest.raises(ValueError):
            service.run_task(FailingParsePlaneClient(), "TASK-5")

    payloads = [json.loads(record.message) for record in caplog.records if record.message.startswith("{")]
    events = {payload["event"] for payload in payloads}

    assert "run_start" in events
    assert "phase" in events
    assert "run_failed" in events
    assert all("run_id" in payload for payload in payloads)
