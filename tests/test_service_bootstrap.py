from pathlib import Path

from control_plane.legacy_execution.service import ExecutionService
from control_plane.config.settings import Settings
from control_plane.domain.models import BoardTask, ValidationResult
from control_plane.execution.models import NoOpDecision, RetryDecision


class DummyPlaneClient:
    def fetch_issue(self, task_id: str) -> dict[str, object]:  # noqa: ARG002
        return {}

    def to_board_task(self, issue: dict[str, object]) -> BoardTask:  # noqa: ARG002
        return BoardTask(
            task_id="TASK-7",
            project_id="proj",
            title="Task",
            description="desc",
            status="Ready for AI",
            repo_key="repo_a",
            base_branch="main",
            execution_mode="goal",
            goal_text="Do thing.",
        )

    def transition_issue(self, task_id: str, state: str) -> None:  # noqa: ARG002
        return

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:  # noqa: ARG002
        return


def test_service_uses_bootstrap_environment_for_validation(tmp_path: Path) -> None:
    settings = Settings.model_validate(
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
                    "validation_commands": ["pytest -q"],
                }
            },
            "report_root": str(tmp_path / "reports"),
        }
    )
    service = ExecutionService(settings)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    service.workspace.create = lambda: tmp_path / "workspace"  # type: ignore[assignment]
    service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
    service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
    added_excludes: list[tuple[Path, str]] = []
    service.git.add_local_exclude = lambda repo_path, pattern: added_excludes.append((repo_path, pattern))  # type: ignore[assignment]
    service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
    service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
    service.git.changed_files = lambda repo_path: []  # type: ignore[assignment]
    service.git.commit_all = lambda repo_path, message: False  # type: ignore[assignment]
    service.kodo.write_goal_file = lambda path, goal_text, constraints_text: path  # type: ignore[assignment]
    service.kodo.run = lambda goal_file, repo_path, env=None, profile=None, kodo_mode="goal": type(  # type: ignore[assignment]
        "KodoResult",
        (),
        {"exit_code": 0, "stdout": "", "stderr": "", "command": ["kodo"]},
    )()
    service.bootstrapper.prepare = lambda *args, **kwargs: type(  # type: ignore[assignment]
        "BootstrapResult",
        (),
        {"env": {"PATH": f"{repo_path / '.venv' / 'bin'}:/usr/bin", "VIRTUAL_ENV": str(repo_path / ".venv")}, "commands": []},
    )()

    captured_env: dict[str, str] = {}

    def fake_run(commands: list[str], cwd: Path, env: dict[str, str] | None = None, **kwargs):  # noqa: ARG001
        nonlocal captured_env
        captured_env = dict(env or {})
        return [
            ValidationResult(
                command=commands[0],
                exit_code=0,
                stdout="",
                stderr="",
                duration_ms=1,
            )
        ]

    service.validation.run = fake_run  # type: ignore[assignment]
    service.usage_store.noop_decision = lambda **kwargs: NoOpDecision(should_skip=False)  # type: ignore[assignment]
    service.usage_store.retry_decision = lambda **kwargs: RetryDecision(allowed=True)  # type: ignore[assignment]
    service.usage_store.budget_decision = lambda **kwargs: type("B", (), {"allowed": True})()  # type: ignore[assignment]
    service.usage_store.record_execution = lambda **kwargs: None  # type: ignore[assignment]

    result = service.run_task(DummyPlaneClient(), "TASK-7")

    assert result.validation_passed is True
    assert captured_env["VIRTUAL_ENV"] == str(repo_path / ".venv")
    assert captured_env["PATH"].startswith(f"{repo_path / '.venv' / 'bin'}:")
    assert added_excludes == [(repo_path, ".kodo/")]
