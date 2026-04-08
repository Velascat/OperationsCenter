"""Integration tests for baseline-validation → retry → fix-task-creation path.

Prevents regression of fixes from stages 1 and 2:
1. Baseline failure with empty stderr still triggers retry (non-None excerpt).
2. Fix-task description handles mixed pass/fail validation results without crash.
3. ValidationHistoryCollector correctly computes failure_rate at 60%.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


from control_plane.application.service import ExecutionService
from control_plane.config.settings import Settings
from control_plane.domain.models import BoardTask, ValidationResult
from control_plane.execution.models import NoOpDecision, RetryDecision
from control_plane.observer.collectors.validation_history import ValidationHistoryCollector


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class DummyPlaneClient:
    """Minimal PlaneClient stub reused across tests."""

    def __init__(self) -> None:
        self.created_issues: list[dict] = []

    def fetch_issue(self, task_id: str) -> dict[str, object]:  # noqa: ARG002
        return {}

    def to_board_task(self, issue: dict[str, object]) -> BoardTask:  # noqa: ARG002
        return BoardTask(
            task_id="TASK-10",
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

    def list_issues(self) -> list[dict]:
        return []

    def create_issue(self, **kwargs) -> dict:
        self.created_issues.append(kwargs)
        return {"id": "NEW-1"}


def _make_settings(tmp_path: Path) -> Settings:
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
                    "validation_commands": ["pytest -q"],
                }
            },
            "report_root": str(tmp_path / "reports"),
        }
    )


def _stub_service(service: ExecutionService, tmp_path: Path) -> Path:
    """Wire all adapters to harmless stubs. Returns the fake repo_path."""
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
    service.kodo.run = lambda goal_file, repo_path, env=None, profile=None: type(  # type: ignore[assignment]
        "KodoResult",
        (),
        {"exit_code": 0, "stdout": "", "stderr": "", "command": ["kodo"]},
    )()
    service.kodo.is_orchestrator_rate_limited = lambda result: False  # type: ignore[assignment]
    service.kodo.command_to_json = lambda cmd: cmd  # type: ignore[assignment]
    service.bootstrapper.prepare = lambda *args, **kwargs: type(  # type: ignore[assignment]
        "BootstrapResult",
        (),
        {"env": {"PATH": "/usr/bin"}, "commands": []},
    )()
    service.reporter.write_request = lambda run_dir, req: "request.json"  # type: ignore[assignment]
    service.reporter.write_kodo = lambda run_dir, cmd, stdout, stderr, prefix=None: ["kodo.json"]  # type: ignore[assignment]
    service.reporter.write_validation = lambda run_dir, results: "validation.json"  # type: ignore[assignment]
    service.reporter.write_initial_validation = lambda run_dir, results: "initial_validation.json"  # type: ignore[assignment]
    service.reporter.write_outcome = lambda run_dir, result: "outcome.json"  # type: ignore[assignment]
    service.usage_store.noop_decision = lambda **kwargs: NoOpDecision(should_skip=False)  # type: ignore[assignment]
    service.usage_store.retry_decision = lambda **kwargs: RetryDecision(allowed=True)  # type: ignore[assignment]
    service.usage_store.budget_decision = lambda **kwargs: type("B", (), {"allowed": True})()  # type: ignore[assignment]
    service.usage_store.record_execution = lambda **kwargs: None  # type: ignore[assignment]

    return repo_path


# ---------------------------------------------------------------------------
# Helpers for test 3
# ---------------------------------------------------------------------------

@dataclass
class _MinimalSettings:
    report_root: Path


@dataclass
class _MinimalContext:
    settings: _MinimalSettings
    repo_name: str


def _make_run_dir(
    base: Path,
    run_id: str,
    task_id: str,
    repo_key: str,
    outcome_status: str = "executed",
    worker_role: str = "worker",
    validation_passed: bool = True,
) -> Path:
    """Create a fake run directory with the required artifact files."""
    run_dir = base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "control_outcome.json").write_text(
        json.dumps(
            {
                "status": outcome_status,
                "task_id": task_id,
                "worker_role": worker_role,
            }
        )
    )
    (run_dir / "request.json").write_text(
        json.dumps({"task": {"repo_key": repo_key}})
    )
    (run_dir / "validation.json").write_text(
        json.dumps({"passed": validation_passed})
    )
    return run_dir


# ===========================================================================
# Test 1: Baseline failure with empty stderr triggers retry with non-None error_text
# ===========================================================================

class TestBaselineFailureEmptyStderrRetry:
    def test_empty_stderr_triggers_retry_and_passes(self, tmp_path: Path) -> None:
        """When baseline validation fails with empty stderr but non-empty stdout,
        _validation_excerpt should return a non-empty string (falling back to stdout),
        the retry path fires (kodo.run called twice), and the final result passes."""
        service = ExecutionService(_make_settings(tmp_path))
        _stub_service(service, tmp_path)

        # Track how many times kodo.run is called
        kodo_run_count = 0
        original_kodo_run = service.kodo.run

        def counting_kodo_run(goal_file, repo_path, env=None, profile=None):
            nonlocal kodo_run_count
            kodo_run_count += 1
            return original_kodo_run(goal_file, repo_path, env=env, profile=profile)

        service.kodo.run = counting_kodo_run  # type: ignore[assignment]

        # Validation call sequence:
        # 1. Baseline validation → fail (exit_code=1, stderr="", stdout="some error output")
        # 2. Post-kodo validation → fail (triggers retry)
        # 3. Retry validation → pass
        validation_call_count = 0

        def sequenced_validation(commands, cwd, env=None, **kwargs):
            nonlocal validation_call_count
            validation_call_count += 1
            if validation_call_count == 1:
                # Baseline: fail with empty stderr, non-empty stdout
                return [
                    ValidationResult(
                        command=commands[0],
                        exit_code=1,
                        stdout="some error output",
                        stderr="",
                        duration_ms=1,
                    )
                ]
            elif validation_call_count == 2:
                # Post-kodo: still failing
                return [
                    ValidationResult(
                        command=commands[0],
                        exit_code=1,
                        stdout="still failing",
                        stderr="",
                        duration_ms=1,
                    )
                ]
            else:
                # Retry validation: pass
                return [
                    ValidationResult(
                        command=commands[0],
                        exit_code=0,
                        stdout="",
                        stderr="",
                        duration_ms=1,
                    )
                ]

        service.validation.run = sequenced_validation  # type: ignore[assignment]

        # Verify _validation_excerpt returns non-empty for empty-stderr / non-empty-stdout
        excerpt = ExecutionService._validation_excerpt([
            ValidationResult(command="pytest -q", exit_code=1, stdout="some error output", stderr="", duration_ms=1)
        ])
        assert excerpt, "_validation_excerpt must return non-empty string when stderr is empty but stdout has content"

        # Run the full task
        client = DummyPlaneClient()
        result = service.run_task(client, "TASK-10")

        # Retry path fired: kodo.run called twice (initial + retry)
        assert kodo_run_count == 2, f"Expected kodo.run called twice (retry), got {kodo_run_count}"
        # Final validation passed after retry
        assert result.validation_passed is True
        assert result.validation_retried is True


# ===========================================================================
# Test 2: Fix-task description with mixed pass/fail validation results
# ===========================================================================

class TestFixTaskDescriptionMixedResults:
    def test_mixed_results_no_crash_and_correct_content(self, tmp_path: Path) -> None:
        """_maybe_create_fix_validation_task should not crash when validation_results
        has mixed pass/fail. Description should contain failing command info but NOT
        the passing command's output."""
        service = ExecutionService(_make_settings(tmp_path))

        mixed_results = [
            ValidationResult(
                command="pytest -q",
                exit_code=0,
                stdout="all tests passed",
                stderr="",
                duration_ms=100,
            ),
            ValidationResult(
                command="ruff check .",
                exit_code=1,
                stdout="",
                stderr="lint error",
                duration_ms=50,
            ),
        ]

        client = DummyPlaneClient()
        task = BoardTask(
            task_id="TASK-20",
            project_id="proj",
            title="Task",
            description="desc",
            status="Running",
            repo_key="repo_a",
            base_branch="main",
            execution_mode="goal",
            goal_text="Do thing.",
        )

        baseline_error_text = ExecutionService._validation_excerpt(mixed_results)

        # Call _maybe_create_fix_validation_task directly
        new_id = service._maybe_create_fix_validation_task(
            client,
            task,
            baseline_error_text,
            "run-123",
            validation_results=mixed_results,
            repo_target=service._repo_target_for(task),
        )

        # Should not crash and should create an issue
        assert new_id == "NEW-1"
        assert len(client.created_issues) == 1

        description = client.created_issues[0]["description"]

        # Description contains the failing command info
        assert "ruff check ." in description
        assert "lint error" in description

        # Description does NOT contain the passing command's output
        assert "all tests passed" not in description


# ===========================================================================
# Test 3: ValidationHistoryCollector computes failure_rate correctly
# ===========================================================================

class TestValidationHistoryFailureRate:
    def test_60_percent_failure_rate_flagged(self, tmp_path: Path) -> None:
        """5 runs, 3 failures (60%) — above 50% threshold, should be flagged
        with failure_rate == 0.6."""
        for i in range(5):
            # First 3 runs fail, last 2 pass
            passed = i >= 3
            _make_run_dir(
                tmp_path,
                f"run-{i}",
                "task-x",
                "myrepo",
                validation_passed=passed,
            )

        ctx = _MinimalContext(
            settings=_MinimalSettings(report_root=tmp_path),
            repo_name="myrepo",
        )
        signal = ValidationHistoryCollector().collect(ctx)

        assert signal.status == "patterns_detected"
        assert len(signal.tasks_with_repeated_failures) == 1

        rec = signal.tasks_with_repeated_failures[0]
        assert rec.task_id == "task-x"
        assert rec.total_runs == 5
        assert rec.validation_failure_count == 3
        assert rec.failure_rate == 0.6
