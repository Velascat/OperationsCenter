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
mode: bad_mode

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
    with pytest.raises(ValueError, match="Unsupported execution mode"):
        service.run_task(DummyPlaneClient(), "TASK-1")


def test_task_parser_accepts_test_campaign():
    from control_plane.application.task_parser import TaskParser
    p = TaskParser()
    body = """## Execution
repo: MyRepo
mode: test_campaign
spec_campaign_id: abc-123
spec_file: docs/specs/add-auth.md
task_phase: test_campaign

## Goal
Run adversarial tests against the new auth layer.
"""
    parsed = p.parse(body)
    assert parsed.execution_metadata["mode"] == "test_campaign"
    assert parsed.execution_metadata["spec_campaign_id"] == "abc-123"


def test_task_parser_accepts_improve_campaign():
    from control_plane.application.task_parser import TaskParser
    p = TaskParser()
    body = """## Execution
repo: MyRepo
mode: improve_campaign
spec_campaign_id: abc-123
spec_file: docs/specs/add-auth.md
task_phase: improve_campaign
spec_coverage_hint: Goal 1

## Goal
Simplify the new auth middleware.
"""
    parsed = p.parse(body)
    assert parsed.execution_metadata["mode"] == "improve_campaign"
    assert parsed.execution_metadata["spec_coverage_hint"] == "Goal 1"


def test_kodo_adapter_build_command_test_mode():
    from pathlib import Path
    from control_plane.adapters.kodo.adapter import KodoAdapter
    from control_plane.config.settings import KodoSettings
    adapter = KodoAdapter(KodoSettings(binary="kodo", team="full", cycles=3,
                                       exchanges=20, orchestrator="claude", effort="standard",
                                       timeout_seconds=600))
    cmd = adapter.build_command(Path("/tmp/goal.md"), Path("/tmp/repo"), kodo_mode="test")
    assert cmd[0] == "kodo"
    assert "--test" in cmd
    # --goal-file and --test are mutually exclusive in kodo's argparse — must not coexist
    assert "--goal-file" not in cmd
    assert "--improve" not in cmd


def test_kodo_adapter_build_command_improve_mode():
    from pathlib import Path
    from control_plane.adapters.kodo.adapter import KodoAdapter
    from control_plane.config.settings import KodoSettings
    adapter = KodoAdapter(KodoSettings(binary="kodo", team="full", cycles=3,
                                       exchanges=20, orchestrator="claude", effort="standard",
                                       timeout_seconds=600))
    cmd = adapter.build_command(Path("/tmp/goal.md"), Path("/tmp/repo"), kodo_mode="improve")
    assert cmd[0] == "kodo"
    assert "--improve" in cmd
    # --goal-file and --improve are mutually exclusive in kodo's argparse — must not coexist
    assert "--goal-file" not in cmd
    assert "--test" not in cmd


def test_kodo_adapter_build_command_goal_mode_unchanged():
    from pathlib import Path
    from control_plane.adapters.kodo.adapter import KodoAdapter
    from control_plane.config.settings import KodoSettings
    adapter = KodoAdapter(KodoSettings(binary="kodo", team="full", cycles=3,
                                       exchanges=20, orchestrator="claude", effort="standard",
                                       timeout_seconds=600))
    cmd = adapter.build_command(Path("/tmp/goal.md"), Path("/tmp/repo"))
    assert "--test" not in cmd
    assert "--improve" not in cmd
    assert "--goal-file" in cmd
