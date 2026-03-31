from pathlib import Path

import pytest

from control_plane.application.service import ExecutionService
from control_plane.config.settings import Settings
from control_plane.domain.models import ExecutionResult


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

        c = PlaneClient("http://plane.local", "token", "ws", "proj")
        try:
            return c.to_board_task(issue)
        finally:
            c.close()

    def transition_issue(self, task_id: str, state: str) -> None:  # noqa: ARG002
        return

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:  # noqa: ARG002
        return


class ValidPlaneClient(FailingParsePlaneClient):
    def fetch_issue(self, task_id: str) -> dict[str, object]:
        return {
            "id": task_id,
            "name": "Task",
            "project_id": "proj",
            "description": """## Execution
repo: repo_a
base_branch: main
mode: goal

## Goal
Do thing.
""",
            "state": {"name": "Ready for AI"},
            "labels": [],
        }


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


def test_early_failure_writes_retained_failure_artifact(settings: Settings) -> None:
    service = ExecutionService(settings)
    with pytest.raises(ValueError, match="supports only 'goal'"):
        service.run_task(FailingParsePlaneClient(), "TASK-2")

    run_dirs = list(settings.report_root.glob("*_TASK-2_*"))
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "request_context.json").exists()
    assert (run_dir / "failure.json").exists()


def test_draft_push_summary_is_explicit() -> None:
    task = type("Task", (), {"task_id": "TASK-3"})
    result = ExecutionResult(
        run_id="abc",
        success=False,
        validation_passed=False,
        branch_pushed=True,
        draft_branch_pushed=True,
        push_reason="draft_on_validation_failure",
        summary="run_id=abc execution=passed validation=failed policy=passed branch_push=draft_on_validation_failure",
    )

    comment = ExecutionService._comment_markdown(task, result)
    assert "- draft_branch_pushed: True" in comment
    assert "- push_reason: draft_on_validation_failure" in comment


def test_comment_markdown_includes_changed_files_and_diff_stat() -> None:
    task = type("Task", (), {"task_id": "TASK-4"})
    result = ExecutionResult(
        run_id="xyz",
        success=False,
        changed_files=[
            "src/a.py",
            "src/b.py",
            "tests/test_a.py",
            "tests/test_b.py",
            "src/c.py",
            "src/d.py",
        ],
        diff_stat_excerpt="src/a.py | 2 +-\n1 file changed, 1 insertion(+), 1 deletion(-)",
        validation_passed=True,
        branch_pushed=False,
        draft_branch_pushed=False,
        push_reason=None,
        summary="run_id=xyz execution=passed validation=passed policy=failed branch_push=not_pushed changed_files=6",
        policy_violations=["kodo/config.json"],
        final_status="Blocked",
    )

    comment = ExecutionService._comment_markdown(task, result)

    assert "- changed_files: src/a.py, src/b.py, tests/test_a.py, tests/test_b.py, src/c.py, ... (+1 more)" in comment
    assert "- diff_stat: src/a.py | 2 +-" in comment


def test_budget_skip_returns_without_repo_setup(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(settings.report_root / "execution" / "usage.json"))
    monkeypatch.setenv("CONTROL_PLANE_MAX_EXEC_PER_HOUR", "0")
    monkeypatch.setenv("CONTROL_PLANE_MAX_EXEC_PER_DAY", "0")
    service = ExecutionService(settings)

    def fail_clone(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("repo setup should not run when budget is exceeded")

    service.git.clone = fail_clone  # type: ignore[assignment]
    result = service.run_task(ValidPlaneClient(), "TASK-5")

    assert result.outcome_status == "skipped"
    assert result.outcome_reason == "budget_exceeded"
    run_dirs = list(settings.report_root.glob("*_TASK-5_*"))
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "control_outcome.json").exists()


def test_internal_kodo_only_changes_are_classified_as_no_op(tmp_path: Path) -> None:
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
                }
            },
            "report_root": str(tmp_path / "reports"),
        }
    )
    service = ExecutionService(settings)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    transitions: list[str] = []
    comments: list[str] = []

    class Client(ValidPlaneClient):
        def transition_issue(self, task_id: str, state: str) -> None:  # noqa: ARG002
            transitions.append(state)

        def comment_issue(self, task_id: str, comment_markdown: str) -> None:  # noqa: ARG002
            comments.append(comment_markdown)

    service.workspace.create = lambda: tmp_path / "workspace"  # type: ignore[assignment]
    service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
    service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
    service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
    service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
    service.git.changed_files = lambda repo_path: ["kodo/config.json", "kodo/run-status.md"]  # type: ignore[assignment]
    service.git.diff_stat = lambda repo_path: "untracked | kodo/config.json\nuntracked | kodo/run-status.md"  # type: ignore[assignment]
    service.git.diff_patch = lambda repo_path: ""  # type: ignore[assignment]
    service.git.commit_all = lambda repo_path, message: (_ for _ in ()).throw(AssertionError("should not commit internal-only changes"))  # type: ignore[assignment]
    service.kodo.write_goal_file = lambda path, goal_text, constraints_text: path  # type: ignore[assignment]
    service.kodo.run = lambda goal_file, repo_path: type(  # type: ignore[assignment]
        "KodoResult",
        (),
        {"exit_code": 0, "stdout": "", "stderr": "", "command": ["kodo"]},
    )()
    service.validation.run = lambda commands, cwd, env=None: []  # type: ignore[assignment]
    service.bootstrapper.prepare = lambda *args, **kwargs: type("BootstrapResult", (), {"env": {}, "commands": []})()  # type: ignore[assignment]

    result = service.run_task(Client(), "TASK-8", preauthorized=True)

    assert result.outcome_status == "no_op"
    assert result.outcome_reason == "internal_only_change"
    assert result.changed_files == []
    assert result.internal_changed_files == ["kodo/config.json", "kodo/run-status.md"]
    assert result.final_status == "Blocked"
    assert transitions[-1] == "Blocked"
    assert any("internal_changed_files: kodo/config.json, kodo/run-status.md" in comment for comment in comments)
