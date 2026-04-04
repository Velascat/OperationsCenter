"""Tests for per-command validation profiles and retry narrowing."""

from __future__ import annotations

from pathlib import Path

from control_plane.application.service import ExecutionService
from control_plane.config.settings import Settings
from control_plane.domain.models import BoardTask, RepoTarget, ValidationResult
from control_plane.execution.models import NoOpDecision, RetryDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class DummyPlaneClient:
    """Minimal stand-in for PlaneClient used by ExecutionService.run_task."""

    def __init__(self, task: BoardTask) -> None:
        self._task = task

    def fetch_issue(self, task_id: str) -> dict[str, object]:  # noqa: ARG002
        return {}

    def to_board_task(self, issue: dict[str, object]) -> BoardTask:  # noqa: ARG002
        return self._task

    def transition_issue(self, task_id: str, state: str) -> None:  # noqa: ARG002
        return

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:  # noqa: ARG002
        return


def _make_settings(tmp_path: Path, profiles: dict[str, list[str]] | None = None) -> Settings:
    repos_cfg: dict = {
        "repo_a": {
            "clone_url": "git@github.com:x/repo_a.git",
            "default_branch": "main",
            "validation_commands": ["pytest -q", "ruff check ."],
        }
    }
    if profiles is not None:
        repos_cfg["repo_a"]["validation_profiles"] = profiles

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
            "repos": repos_cfg,
            "report_root": str(tmp_path / "reports"),
        }
    )


def _make_task(profile: str | None = None) -> BoardTask:
    return BoardTask(
        task_id="TASK-1",
        project_id="proj",
        title="Task",
        description="desc",
        status="Ready for AI",
        repo_key="repo_a",
        base_branch="main",
        execution_mode="goal",
        goal_text="Do thing.",
        validation_profile=profile,
    )


def _wire_service(service: ExecutionService, tmp_path: Path, captured: dict) -> Path:
    """Monkey-patch service collaborators so run_task doesn't need real repos."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir(exist_ok=True)

    service.workspace.create = lambda: tmp_path / "workspace"  # type: ignore[assignment]
    service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
    service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
    service.git.add_local_exclude = lambda repo_path, pattern: None  # type: ignore[assignment]
    service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
    service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
    service.git.changed_files = lambda repo_path: []  # type: ignore[assignment]
    service.git.commit_all = lambda repo_path, message: False  # type: ignore[assignment]
    service.kodo.write_goal_file = lambda path, goal_text, constraints_text: path  # type: ignore[assignment]
    service.kodo.run = lambda goal_file, repo_path: type(  # type: ignore[assignment]
        "KodoResult", (), {"exit_code": 0, "stdout": "", "stderr": "", "command": ["kodo"]},
    )()
    service.bootstrapper.prepare = lambda *args, **kwargs: type(  # type: ignore[assignment]
        "BootstrapResult", (), {"env": {}, "commands": []},
    )()
    service.usage_store.noop_decision = lambda **kwargs: NoOpDecision(should_skip=False)  # type: ignore[assignment]
    service.usage_store.retry_decision = lambda **kwargs: RetryDecision(allowed=True)  # type: ignore[assignment]
    service.usage_store.budget_decision = lambda **kwargs: type("B", (), {"allowed": True})()  # type: ignore[assignment]
    service.usage_store.record_execution = lambda **kwargs: None  # type: ignore[assignment]

    def fake_run(commands: list[str], cwd: Path, env: dict[str, str] | None = None, **kwargs):  # noqa: ARG001
        captured.setdefault("calls", []).append(commands)
        return [
            ValidationResult(command=cmd, exit_code=0, stdout="", stderr="", duration_ms=1)
            for cmd in commands
        ]

    service.validation.run = fake_run  # type: ignore[assignment]
    return repo_path


# ---------------------------------------------------------------------------
# Profile selection tests
# ---------------------------------------------------------------------------

class TestProfileSelection:
    def test_profile_selects_profile_commands(self, tmp_path: Path) -> None:
        """When the task has a valid profile, those commands are used."""
        settings = _make_settings(tmp_path, profiles={"quick": ["ruff check ."]})
        service = ExecutionService(settings)
        captured: dict = {}
        _wire_service(service, tmp_path, captured)
        plane = DummyPlaneClient(_make_task(profile="quick"))

        service.run_task(plane, "TASK-1")

        # Only the profile's commands should have been used
        assert captured["calls"][0] == ["ruff check ."]

    def test_invalid_profile_falls_back(self, tmp_path: Path) -> None:
        """When the task has an unknown profile, fall back to validation_commands."""
        settings = _make_settings(tmp_path, profiles={"quick": ["ruff check ."]})
        service = ExecutionService(settings)
        captured: dict = {}
        _wire_service(service, tmp_path, captured)
        plane = DummyPlaneClient(_make_task(profile="nonexistent"))

        service.run_task(plane, "TASK-1")

        assert captured["calls"][0] == ["pytest -q", "ruff check ."]

    def test_no_profile_falls_back(self, tmp_path: Path) -> None:
        """When the task has no profile set, fall back to validation_commands."""
        settings = _make_settings(tmp_path, profiles={"quick": ["ruff check ."]})
        service = ExecutionService(settings)
        captured: dict = {}
        _wire_service(service, tmp_path, captured)
        plane = DummyPlaneClient(_make_task(profile=None))

        service.run_task(plane, "TASK-1")

        assert captured["calls"][0] == ["pytest -q", "ruff check ."]


# ---------------------------------------------------------------------------
# Retry narrowing tests (unit-level, no run_task needed)
# ---------------------------------------------------------------------------

class TestRetryNarrowing:
    def test_all_lint_failures_narrows_to_lint_only(self) -> None:
        """When every failure is a lint command, retry only those commands."""
        full = ["pytest -q", "ruff check .", "black --check ."]
        results = [
            ValidationResult(command="pytest -q", exit_code=0, stdout="", stderr="", duration_ms=1),
            ValidationResult(command="ruff check .", exit_code=1, stdout="", stderr="err", duration_ms=1),
            ValidationResult(command="black --check .", exit_code=1, stdout="", stderr="err", duration_ms=1),
        ]
        narrowed = ExecutionService._narrow_retry_commands(full, results)
        assert narrowed == ["ruff check .", "black --check ."]

    def test_non_lint_failure_runs_full_suite(self) -> None:
        """When failures include a non-lint command, retry uses the full suite."""
        full = ["pytest -q", "ruff check ."]
        results = [
            ValidationResult(command="pytest -q", exit_code=1, stdout="", stderr="err", duration_ms=1),
            ValidationResult(command="ruff check .", exit_code=1, stdout="", stderr="err", duration_ms=1),
        ]
        narrowed = ExecutionService._narrow_retry_commands(full, results)
        assert narrowed == full

    def test_isort_counted_as_lint(self) -> None:
        """isort failures are recognized as lint commands."""
        full = ["pytest -q", "isort --check ."]
        results = [
            ValidationResult(command="pytest -q", exit_code=0, stdout="", stderr="", duration_ms=1),
            ValidationResult(command="isort --check .", exit_code=1, stdout="", stderr="err", duration_ms=1),
        ]
        narrowed = ExecutionService._narrow_retry_commands(full, results)
        assert narrowed == ["isort --check ."]

    def test_no_failures_returns_full(self) -> None:
        """When nothing failed, return the full command list."""
        full = ["pytest -q", "ruff check ."]
        results = [
            ValidationResult(command="pytest -q", exit_code=0, stdout="", stderr="", duration_ms=1),
            ValidationResult(command="ruff check .", exit_code=0, stdout="", stderr="", duration_ms=1),
        ]
        narrowed = ExecutionService._narrow_retry_commands(full, results)
        assert narrowed == full


# ---------------------------------------------------------------------------
# _resolve_validation_commands unit tests
# ---------------------------------------------------------------------------

class TestResolveValidationCommands:
    def test_resolves_profile(self) -> None:
        task = _make_task(profile="quick")
        target = RepoTarget(
            repo_key="r",
            clone_url="git@x",
            default_branch="main",
            workdir_name="r",
            validation_commands=["pytest -q"],
            validation_profiles={"quick": ["ruff check ."]},
        )
        assert ExecutionService._resolve_validation_commands(task, target) == ["ruff check ."]

    def test_falls_back_on_missing_profile(self) -> None:
        task = _make_task(profile="missing")
        target = RepoTarget(
            repo_key="r",
            clone_url="git@x",
            default_branch="main",
            workdir_name="r",
            validation_commands=["pytest -q"],
            validation_profiles={"quick": ["ruff check ."]},
        )
        assert ExecutionService._resolve_validation_commands(task, target) == ["pytest -q"]
