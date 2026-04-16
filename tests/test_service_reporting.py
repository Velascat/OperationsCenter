from pathlib import Path

import pytest

from control_plane.application.service import ExecutionService
from control_plane.config.settings import Settings
from control_plane.domain.models import ExecutionResult, ValidationResult


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
    with pytest.raises(ValueError, match="Unsupported execution mode"):
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


def test_internal_kodo_only_changes_are_classified_as_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
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
    service.kodo.run = lambda goal_file, repo_path, env=None, profile=None, kodo_mode="goal": type(  # type: ignore[assignment]
        "KodoResult",
        (),
        {"exit_code": 0, "stdout": "", "stderr": "", "command": ["kodo"]},
    )()
    service.validation.run = lambda commands, cwd, env=None, **kwargs: []  # type: ignore[assignment]
    service.bootstrapper.prepare = lambda *args, **kwargs: type("BootstrapResult", (), {"env": {}, "commands": []})()  # type: ignore[assignment]

    result = service.run_task(Client(), "TASK-8", preauthorized=True)

    assert result.outcome_status == "no_op"
    assert result.outcome_reason == "internal_only_change"
    assert result.changed_files == []
    assert result.internal_changed_files == ["kodo/config.json", "kodo/run-status.md"]
    assert result.final_status == "Done"
    assert transitions[-1] == "Done"
    assert any("internal_changed_files: kodo/config.json, kodo/run-status.md" in comment for comment in comments)


def test_no_op_with_validation_failure_cancels_and_creates_fix_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """no_op + validation_failed → Cancelled (not Blocked) + fix-validation task created.

    A no_op run makes no changes to the repo, so any validation failure is
    definitively pre-existing.  The old behaviour (Blocked) triggered an infinite
    triage → follow-up → also-fails loop.  The new behaviour cancels the task and
    ensures a 'Fix pre-existing validation failure' task exists.
    """
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
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
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    goal_file = workspace / "goal.md"
    goal_file.write_text("# Goal\nDo thing.\n")

    transitions: list[str] = []
    comments: list[str] = []
    created_issues: list[dict[str, object]] = []

    class Client(ValidPlaneClient):
        def transition_issue(self, task_id: str, state: str) -> None:  # noqa: ARG002
            transitions.append(state)

        def comment_issue(self, task_id: str, comment_markdown: str) -> None:  # noqa: ARG002
            comments.append(comment_markdown)

        def list_issues(self) -> list[dict[str, object]]:
            return []

        def create_issue(self, name: str, description: str = "", state: str = "Ready for AI", label_names: list[str] | None = None) -> dict[str, object]:
            issue = {"id": f"FIX-{len(created_issues) + 1}", "name": name}
            created_issues.append(issue)
            return issue

    service.workspace.create = lambda: workspace  # type: ignore[assignment]
    service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
    service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
    service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
    service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
    service.git.changed_files = lambda repo_path: ["kodo/config.json", "kodo/run-status.md"]  # type: ignore[assignment]
    service.git.diff_stat = lambda repo_path: "untracked | kodo/config.json\nuntracked | kodo/run-status.md"  # type: ignore[assignment]
    service.git.diff_patch = lambda repo_path: ""  # type: ignore[assignment]
    service.git.commit_all = lambda repo_path, message: (_ for _ in ()).throw(AssertionError("should not commit"))  # type: ignore[assignment]
    service.kodo.write_goal_file = lambda path, goal_text, constraints_text=None: path  # type: ignore[assignment]
    service.kodo.run = lambda goal_file, repo_path, env=None, profile=None, kodo_mode="goal": type(  # type: ignore[assignment]
        "KodoResult",
        (),
        {"exit_code": 0, "stdout": "", "stderr": "", "command": ["kodo"]},
    )()
    service.kodo.command_to_json = lambda cmd: "{}"  # type: ignore[assignment]
    service.kodo.is_orchestrator_rate_limited = lambda result: False  # type: ignore[assignment]
    service.validation.run = lambda commands, cwd, env=None, **kwargs: [ValidationResult(command="pytest", exit_code=1, stdout="", stderr="FAIL", duration_ms=100)]  # type: ignore[assignment]
    service.validation.passed = lambda results: False  # type: ignore[assignment]
    service.bootstrapper.prepare = lambda *args, **kwargs: type("BootstrapResult", (), {"env": {}, "commands": []})()  # type: ignore[assignment]

    result = service.run_task(Client(), "TASK-NOOP-FAIL", preauthorized=True)

    assert result.outcome_status == "no_op"
    assert result.final_status == "Cancelled"
    assert transitions[-1] == "Cancelled"
    # A fix-validation task must be created to break the triage loop
    assert len(created_issues) == 1
    assert "Fix pre-existing validation failure" in created_issues[0]["name"]
    assert result.follow_up_task_ids != []


def test_comment_markdown_includes_validation_errors_on_failure() -> None:
    task = type("Task", (), {"task_id": "TASK-V1"})
    validation_results = [
        ValidationResult(command="pytest", exit_code=1, stdout="", stderr="FAILED test_foo.py::test_bar\nAssertionError: expected 1 got 2", duration_ms=500),
        ValidationResult(command="mypy", exit_code=0, stdout="Success", stderr="", duration_ms=200),
    ]
    result = ExecutionResult(
        run_id="val1",
        success=False,
        validation_passed=False,
        validation_results=validation_results,
        branch_pushed=False,
        draft_branch_pushed=False,
        push_reason=None,
        summary="run_id=val1 execution=passed validation=failed",
    )

    comment = ExecutionService._comment_markdown(task, result)
    assert "validation_errors:" in comment
    assert "```" in comment
    assert "AssertionError: expected 1 got 2" in comment


def test_comment_markdown_omits_validation_errors_on_success() -> None:
    task = type("Task", (), {"task_id": "TASK-V2"})
    validation_results = [
        ValidationResult(command="pytest", exit_code=0, stdout="all passed", stderr="", duration_ms=300),
        ValidationResult(command="mypy", exit_code=0, stdout="Success", stderr="", duration_ms=200),
    ]
    result = ExecutionResult(
        run_id="val2",
        success=True,
        validation_passed=True,
        validation_results=validation_results,
        branch_pushed=False,
        draft_branch_pushed=False,
        push_reason=None,
        summary="run_id=val2 execution=passed validation=passed",
    )

    comment = ExecutionService._comment_markdown(task, result)
    assert "validation_errors:" not in comment


def test_validation_retry_succeeds_moves_to_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    settings = Settings.model_validate(
        {
            "plane": {
                "base_url": "http://plane.local",
                "api_token_env": "PLANE_API_TOKEN",
                "workspace_slug": "ws",
                "project_id": "proj",
            },
            "git": {"provider": "github", "push_on_validation_failure": False},
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
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    kodo_call_count = 0

    def kodo_run(goal_file, repo_path, env=None, profile=None, kodo_mode="goal"):  # type: ignore[no-untyped-def]
        nonlocal kodo_call_count
        kodo_call_count += 1
        stdout = f"kodo_stdout_run{kodo_call_count}"
        stderr = f"kodo_stderr_run{kodo_call_count}"
        return type("KodoResult", (), {"exit_code": 0, "stdout": stdout, "stderr": stderr, "command": ["kodo"]})()

    validation_call_count = 0

    def validation_run(commands, cwd, env=None, **kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG001
        nonlocal validation_call_count
        validation_call_count += 1
        if validation_call_count == 1:
            return [ValidationResult(command="pytest", exit_code=1, stdout="", stderr="FAILED test_foo.py", duration_ms=100)]
        return [ValidationResult(command="pytest", exit_code=0, stdout="all passed", stderr="", duration_ms=100)]

    validation_passed_count = 0

    def validation_passed(results):  # type: ignore[no-untyped-def]
        nonlocal validation_passed_count
        validation_passed_count += 1
        if validation_passed_count == 1:
            return False
        return True

    service.workspace.create = lambda: workspace  # type: ignore[assignment]
    service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
    service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
    service.git.add_local_exclude = lambda repo_path, pattern: None  # type: ignore[assignment]
    service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
    service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
    service.git.changed_files = lambda repo_path: ["src/a.py"]  # type: ignore[assignment]
    service.git.diff_stat = lambda repo_path: "src/a.py | 2 +-"  # type: ignore[assignment]
    service.git.diff_patch = lambda repo_path: "diff --git a/src/a.py b/src/a.py"  # type: ignore[assignment]
    service.git.commit_all = lambda repo_path, message: True  # type: ignore[assignment]
    service.git.push_branch = lambda repo_path, branch: None  # type: ignore[assignment]
    service.kodo.write_goal_file = lambda path, goal_text, constraints_text: path  # type: ignore[assignment]
    service.kodo.run = kodo_run  # type: ignore[assignment]
    service.kodo.command_to_json = lambda cmd: "{}"  # type: ignore[assignment]
    service.kodo.is_orchestrator_rate_limited = lambda result: False  # type: ignore[assignment]
    service.validation.run = validation_run  # type: ignore[assignment]
    service.validation.passed = validation_passed  # type: ignore[assignment]
    service.bootstrapper.prepare = lambda *args, **kwargs: type("BootstrapResult", (), {"env": {}, "commands": []})()  # type: ignore[assignment]

    result = service.run_task(ValidPlaneClient(), "TASK-RETRY1", preauthorized=True)

    assert result.validation_retried is True
    assert result.validation_passed is True
    assert result.success is True
    assert result.final_status == "Review"
    assert kodo_call_count == 2
    assert validation_call_count == 2

    # Verify both initial and retry kodo logs exist in the run directory
    report_root = tmp_path / "reports"
    run_dirs = list(report_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "kodo_stdout.log").exists()
    assert (run_dir / "kodo_stderr.log").exists()
    assert (run_dir / "kodo_command.json").exists()
    assert (run_dir / "kodo_retry_stdout.log").exists()
    assert (run_dir / "kodo_retry_stderr.log").exists()
    assert (run_dir / "kodo_retry_command.json").exists()
    # Verify content distinguishes initial vs retry
    assert (run_dir / "kodo_stdout.log").read_text() == "kodo_stdout_run1"
    assert (run_dir / "kodo_retry_stdout.log").read_text() == "kodo_stdout_run2"
    # Verify artifacts list includes both sets of paths
    retry_artifacts = [a for a in result.artifacts if "kodo_retry" in a]
    assert len(retry_artifacts) == 3

    # Verify initial failing validation is persisted separately
    assert (run_dir / "validation_initial.json").exists()
    import json

    initial_data = json.loads((run_dir / "validation_initial.json").read_text())
    assert len(initial_data) == 1
    assert initial_data[0]["exit_code"] == 1
    assert initial_data[0]["stderr"] == "FAILED test_foo.py"

    # Verify validation.json contains post-retry (passing) results
    assert (run_dir / "validation.json").exists()
    final_data = json.loads((run_dir / "validation.json").read_text())
    assert len(final_data) == 1
    assert final_data[0]["exit_code"] == 0
    assert final_data[0]["stdout"] == "all passed"

    # Verify both artifacts appear in the artifacts list
    initial_artifacts = [a for a in result.artifacts if "validation_initial" in a]
    assert len(initial_artifacts) == 1
    final_validation_artifacts = [a for a in result.artifacts if a.endswith("validation.json")]
    assert len(final_validation_artifacts) == 1


def test_validation_retry_fails_moves_to_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    settings = Settings.model_validate(
        {
            "plane": {
                "base_url": "http://plane.local",
                "api_token_env": "PLANE_API_TOKEN",
                "workspace_slug": "ws",
                "project_id": "proj",
            },
            "git": {"provider": "github", "push_on_validation_failure": False},
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
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    kodo_call_count = 0

    def kodo_run(goal_file, repo_path, env=None, profile=None, kodo_mode="goal"):  # type: ignore[no-untyped-def]
        nonlocal kodo_call_count
        kodo_call_count += 1
        stdout = f"kodo_stdout_run{kodo_call_count}"
        stderr = f"kodo_stderr_run{kodo_call_count}"
        return type("KodoResult", (), {"exit_code": 0, "stdout": stdout, "stderr": stderr, "command": ["kodo"]})()

    validation_call_count = 0

    def validation_run(commands, cwd, env=None, **kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG001
        nonlocal validation_call_count
        validation_call_count += 1
        return [ValidationResult(command="pytest", exit_code=1, stdout="", stderr="FAILED test_foo.py", duration_ms=100)]

    def validation_passed(results):  # type: ignore[no-untyped-def]
        return False

    service.workspace.create = lambda: workspace  # type: ignore[assignment]
    service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
    service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
    service.git.add_local_exclude = lambda repo_path, pattern: None  # type: ignore[assignment]
    service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
    service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
    service.git.changed_files = lambda repo_path: ["src/a.py"]  # type: ignore[assignment]
    service.git.diff_stat = lambda repo_path: "src/a.py | 2 +-"  # type: ignore[assignment]
    service.git.diff_patch = lambda repo_path: "diff --git a/src/a.py b/src/a.py"  # type: ignore[assignment]
    service.git.commit_all = lambda repo_path, message: True  # type: ignore[assignment]
    service.git.push_branch = lambda repo_path, branch: None  # type: ignore[assignment]
    service.kodo.write_goal_file = lambda path, goal_text, constraints_text: path  # type: ignore[assignment]
    service.kodo.run = kodo_run  # type: ignore[assignment]
    service.kodo.command_to_json = lambda cmd: "{}"  # type: ignore[assignment]
    service.kodo.is_orchestrator_rate_limited = lambda result: False  # type: ignore[assignment]
    service.validation.run = validation_run  # type: ignore[assignment]
    service.validation.passed = validation_passed  # type: ignore[assignment]
    service.bootstrapper.prepare = lambda *args, **kwargs: type("BootstrapResult", (), {"env": {}, "commands": []})()  # type: ignore[assignment]

    result = service.run_task(ValidPlaneClient(), "TASK-RETRY2", preauthorized=True)

    assert result.validation_retried is True
    assert result.validation_passed is False
    assert result.success is False
    assert result.final_status == "Blocked"
    assert kodo_call_count == 2
    assert validation_call_count == 2

    # Verify both initial and retry kodo logs exist in the run directory
    report_root = tmp_path / "reports"
    run_dirs = list(report_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "kodo_stdout.log").exists()
    assert (run_dir / "kodo_stderr.log").exists()
    assert (run_dir / "kodo_retry_stdout.log").exists()
    assert (run_dir / "kodo_retry_stderr.log").exists()
    assert (run_dir / "kodo_retry_command.json").exists()
    # Verify content distinguishes initial vs retry
    assert (run_dir / "kodo_stdout.log").read_text() == "kodo_stdout_run1"
    assert (run_dir / "kodo_retry_stdout.log").read_text() == "kodo_stdout_run2"
    # Verify artifacts list includes retry paths
    retry_artifacts = [a for a in result.artifacts if "kodo_retry" in a]
    assert len(retry_artifacts) == 3

    # Verify initial failing validation is persisted separately
    assert (run_dir / "validation_initial.json").exists()
    import json

    initial_data = json.loads((run_dir / "validation_initial.json").read_text())
    assert len(initial_data) == 1
    assert initial_data[0]["exit_code"] == 1
    assert initial_data[0]["stderr"] == "FAILED test_foo.py"

    # Verify validation.json contains post-retry (still failing) results
    assert (run_dir / "validation.json").exists()
    final_data = json.loads((run_dir / "validation.json").read_text())
    assert len(final_data) == 1
    assert final_data[0]["exit_code"] == 1

    # Verify both artifacts appear in the artifacts list
    initial_artifacts = [a for a in result.artifacts if "validation_initial" in a]
    assert len(initial_artifacts) == 1


def test_no_retry_skips_initial_validation_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
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
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service.workspace.create = lambda: workspace  # type: ignore[assignment]
    service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
    service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
    service.git.add_local_exclude = lambda repo_path, pattern: None  # type: ignore[assignment]
    service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
    service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
    service.git.changed_files = lambda repo_path: ["src/a.py"]  # type: ignore[assignment]
    service.git.diff_stat = lambda repo_path: "src/a.py | 2 +-"  # type: ignore[assignment]
    service.git.diff_patch = lambda repo_path: "diff --git a/src/a.py b/src/a.py"  # type: ignore[assignment]
    service.git.commit_all = lambda repo_path, message: True  # type: ignore[assignment]
    service.git.push_branch = lambda repo_path, branch: None  # type: ignore[assignment]
    service.kodo.write_goal_file = lambda path, goal_text, constraints_text: path  # type: ignore[assignment]
    service.kodo.run = lambda goal_file, repo_path, env=None, profile=None, kodo_mode="goal": type("KodoResult", (), {"exit_code": 0, "stdout": "", "stderr": "", "command": ["kodo"]})()  # type: ignore[assignment]
    service.kodo.command_to_json = lambda cmd: "{}"  # type: ignore[assignment]
    service.kodo.is_orchestrator_rate_limited = lambda result: False  # type: ignore[assignment]
    service.validation.run = lambda commands, cwd, env=None, **kwargs: [ValidationResult(command="pytest", exit_code=0, stdout="all passed", stderr="", duration_ms=100)]  # type: ignore[assignment]
    service.validation.passed = lambda results: True  # type: ignore[assignment]
    service.bootstrapper.prepare = lambda *args, **kwargs: type("BootstrapResult", (), {"env": {}, "commands": []})()  # type: ignore[assignment]

    result = service.run_task(ValidPlaneClient(), "TASK-NORETRY", preauthorized=True)

    assert result.validation_retried is False
    assert result.validation_passed is True

    report_root = tmp_path / "reports"
    run_dirs = list(report_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    # No initial validation artifact when validation passes first time
    assert not (run_dir / "validation_initial.json").exists()
    # But validation.json should still exist
    assert (run_dir / "validation.json").exists()
    # No initial validation artifacts in the list
    initial_artifacts = [a for a in result.artifacts if "validation_initial" in a]
    assert len(initial_artifacts) == 0


def test_validation_excerpt_truncates_long_output() -> None:
    long_stderr = "\n".join(f"error line {i}" for i in range(35))
    validation_results = [
        ValidationResult(command="pytest", exit_code=1, stdout="", stderr=long_stderr, duration_ms=100),
    ]

    excerpt = ExecutionService._validation_excerpt(validation_results, max_lines=10)
    assert excerpt is not None
    lines = excerpt.splitlines()
    assert len(lines) == 10
    # Head+tail strategy: first 5 lines (incl. command header) + '...' + last 4 lines
    assert lines[0] == "[pytest]"
    assert "..." in lines


def test_validation_excerpt_head_tail_60_lines() -> None:
    """For a 60-line error output with max_lines=20, both the first 10 and
    last 9 lines are present with an ellipsis separator."""
    error_lines = [f"error line {i}" for i in range(60)]
    long_stderr = "\n".join(error_lines)
    validation_results = [
        ValidationResult(command="pytest", exit_code=1, stdout="", stderr=long_stderr, duration_ms=100),
    ]

    excerpt = ExecutionService._validation_excerpt(validation_results, max_lines=20)
    assert excerpt is not None
    lines = excerpt.splitlines()
    assert len(lines) == 20

    # First half: 10 lines (command header + first 9 error lines)
    assert lines[0] == "[pytest]"
    for i in range(9):
        assert lines[i + 1] == f"error line {i}"

    # Ellipsis separator
    assert lines[10] == "..."

    # Last half: 9 lines (last 9 error lines)
    for i in range(9):
        expected_line_num = 60 - 9 + i
        assert lines[11 + i] == f"error line {expected_line_num}"
