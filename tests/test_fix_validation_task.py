"""Tests for _maybe_create_fix_validation_task enriched descriptions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from control_plane.application.service import ExecutionService
from control_plane.domain.models import ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service() -> ExecutionService:
    """Create an ExecutionService with all collaborators mocked out."""
    mock_settings = MagicMock()
    mock_settings.execution_controls.return_value.usage_path = "/tmp/usage"
    with (
        patch.object(ExecutionService, "__init__", lambda self, _s: None),
    ):
        svc = ExecutionService(mock_settings)
    # Set the minimal attributes the method under test needs.
    svc.logger = MagicMock()
    return svc


def _make_task(repo_key: str = "my-org/my-repo") -> MagicMock:
    task = MagicMock()
    task.repo_key = repo_key
    task.goal_text = "Fix things"
    return task


def _make_repo_target(commands: list[str] | None = None) -> MagicMock:
    rt = MagicMock()
    rt.validation_commands = commands or ["lint", "test", "typecheck"]
    return rt


def _make_plane_client(
    existing_issues: list[dict] | None = None,
    created_id: str = "new-task-123",
) -> MagicMock:
    pc = MagicMock()
    pc.list_issues.return_value = existing_issues or []
    pc.create_issue.return_value = {"id": created_id}
    return pc


def _make_validation_result(
    command: str,
    exit_code: int,
    stderr: str = "",
    stdout: str = "",
) -> ValidationResult:
    return ValidationResult(
        command=command,
        exit_code=exit_code,
        stderr=stderr,
        stdout=stdout,
        duration_ms=100,
    )


# ---------------------------------------------------------------------------
# Tests for _build_fix_validation_description (class method)
# ---------------------------------------------------------------------------


class TestBuildFixValidationDescription:
    """Unit tests for the description builder (no mocking needed)."""

    def test_fallback_when_validation_results_is_none(self) -> None:
        desc = ExecutionService._build_fix_validation_description(
            repo_key="org/repo",
            baseline_error_text="something broke",
            occurrence_count=1,
            validation_results=None,
            repo_target=None,
        )
        assert "## Validation Error" in desc
        assert "something broke" in desc
        assert "## Failing Commands" not in desc
        assert "## Baseline Failure History" not in desc

    def test_enriched_contains_all_sections(self) -> None:
        results = [
            _make_validation_result("lint", 1, stderr="lint err"),
            _make_validation_result("test", 0, stdout="ok"),
        ]
        repo_target = _make_repo_target(["lint", "test", "typecheck"])
        desc = ExecutionService._build_fix_validation_description(
            repo_key="org/repo",
            baseline_error_text="err",
            occurrence_count=2,
            validation_results=results,
            repo_target=repo_target,
        )
        assert "## Goal" in desc
        assert "## Baseline Failure History" in desc
        assert "occurrence #2" in desc
        assert "## Configured Validation Commands" in desc
        assert "- `lint`" in desc
        assert "- `test`" in desc
        assert "- `typecheck`" in desc
        assert "## Failing Commands" in desc
        assert "### `lint` (exit code: 1)" in desc
        assert "lint err" in desc
        # test passed (exit_code=0) — should NOT appear in failing commands
        assert "### `test`" not in desc
        assert "## Constraints" in desc
        assert "- `lint`" in desc

    def test_empty_validation_results_list(self) -> None:
        repo_target = _make_repo_target(["lint", "test"])
        desc = ExecutionService._build_fix_validation_description(
            repo_key="org/repo",
            baseline_error_text="err",
            occurrence_count=1,
            validation_results=[],
            repo_target=repo_target,
        )
        assert "## Failing Commands" not in desc
        assert "## Baseline Failure History" in desc
        assert "## Constraints" in desc

    def test_all_passing_results_no_failing_section(self) -> None:
        results = [
            _make_validation_result("lint", 0, stdout="ok"),
            _make_validation_result("test", 0, stdout="ok"),
        ]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="org/repo",
            baseline_error_text="err",
            occurrence_count=1,
            validation_results=results,
            repo_target=_make_repo_target(["lint", "test"]),
        )
        assert "## Failing Commands" not in desc
        assert "Fix these specific commands" not in desc
        assert "## Baseline Failure History" in desc

    def test_occurrence_count_zero(self) -> None:
        desc = ExecutionService._build_fix_validation_description(
            repo_key="org/repo",
            baseline_error_text="err",
            occurrence_count=0,
            validation_results=[],
        )
        assert "occurrence #0" in desc

    def test_stderr_preferred_over_stdout(self) -> None:
        results = [
            _make_validation_result("cmd", 1, stderr="err output", stdout="std output"),
        ]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="r",
            baseline_error_text="e",
            occurrence_count=1,
            validation_results=results,
        )
        assert "err output" in desc
        assert "std output" not in desc

    def test_stdout_fallback_when_stderr_empty(self) -> None:
        results = [
            _make_validation_result("cmd", 1, stderr="", stdout="fallback output"),
        ]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="r",
            baseline_error_text="e",
            occurrence_count=1,
            validation_results=results,
        )
        assert "fallback output" in desc

    def test_output_truncated_to_50_lines(self) -> None:
        long_stderr = "\n".join(f"line {i}" for i in range(100))
        results = [
            _make_validation_result("cmd", 1, stderr=long_stderr),
        ]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="r",
            baseline_error_text="e",
            occurrence_count=1,
            validation_results=results,
        )
        assert "line 49" in desc
        assert "line 50" not in desc

    def test_very_long_command_name(self) -> None:
        command = "a" * 500
        results = [
            _make_validation_result(command, 1, stderr="err"),
        ]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="r",
            baseline_error_text="e",
            occurrence_count=1,
            validation_results=results,
        )
        assert f"### `{command}` (exit code: 1)" in desc

    def test_truncation_boundary_exactly_50_lines(self) -> None:
        stderr = "\n".join(f"line {i}" for i in range(50))
        results = [
            _make_validation_result("cmd", 1, stderr=stderr),
        ]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="r",
            baseline_error_text="e",
            occurrence_count=1,
            validation_results=results,
        )
        assert "line 49" in desc
        assert "line 50" not in desc

    def test_truncation_boundary_51_lines(self) -> None:
        stderr = "\n".join(f"line {i}" for i in range(51))
        results = [
            _make_validation_result("cmd", 1, stderr=stderr),
        ]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="r",
            baseline_error_text="e",
            occurrence_count=1,
            validation_results=results,
        )
        assert "line 49" in desc
        assert "line 50" not in desc

    def test_multiple_failing_commands(self) -> None:
        results = [
            _make_validation_result("lint", 1, stderr="lint fail"),
            _make_validation_result("test", 2, stderr="test fail"),
            _make_validation_result("typecheck", 0),
        ]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="r",
            baseline_error_text="e",
            occurrence_count=1,
            validation_results=results,
        )
        assert "### `lint` (exit code: 1)" in desc
        assert "### `test` (exit code: 2)" in desc
        assert "### `typecheck`" not in desc
        # Constraints should list both failing commands
        assert "  - `lint`" in desc
        assert "  - `test`" in desc

    def test_no_configured_commands_when_repo_target_none(self) -> None:
        results = [_make_validation_result("cmd", 1, stderr="err")]
        desc = ExecutionService._build_fix_validation_description(
            repo_key="r",
            baseline_error_text="e",
            occurrence_count=1,
            validation_results=results,
            repo_target=None,
        )
        assert "## Configured Validation Commands" not in desc
        assert "## Failing Commands" in desc


# ---------------------------------------------------------------------------
# Tests for _maybe_create_fix_validation_task (integration-ish with mocks)
# ---------------------------------------------------------------------------


class TestMaybeCreateFixValidationTask:
    def test_creates_task_with_enriched_description(self) -> None:
        svc = _make_service()
        pc = _make_plane_client()
        task = _make_task()
        repo_target = _make_repo_target(["lint", "test"])
        results = [_make_validation_result("lint", 1, stderr="bad")]

        tid = svc._maybe_create_fix_validation_task(
            pc, task, "err", "run-1",
            validation_results=results,
            repo_target=repo_target,
        )

        assert tid == "new-task-123"
        call_kwargs = pc.create_issue.call_args[1]
        desc = call_kwargs["description"]
        assert "## Failing Commands" in desc
        assert "## Baseline Failure History" in desc
        assert "occurrence #1" in desc

    def test_skips_when_open_duplicate_exists(self) -> None:
        svc = _make_service()
        task = _make_task("org/repo")
        existing = [
            {
                "id": "existing-1",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "In Progress"},
            }
        ]
        pc = _make_plane_client(existing_issues=existing)

        tid = svc._maybe_create_fix_validation_task(pc, task, "err", "run-1")
        assert tid is None
        pc.create_issue.assert_not_called()

    def test_creates_when_all_existing_are_closed(self) -> None:
        svc = _make_service()
        task = _make_task("org/repo")
        existing = [
            {
                "id": "old-1",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "Done"},
            },
            {
                "id": "old-2",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "Cancelled"},
            },
        ]
        pc = _make_plane_client(existing_issues=existing)

        tid = svc._maybe_create_fix_validation_task(pc, task, "err", "run-1")
        assert tid == "new-task-123"

    def test_dedup_blocked_state_is_closed(self) -> None:
        svc = _make_service()
        task = _make_task("org/repo")
        existing = [
            {
                "id": "old-1",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "Blocked"},
            },
        ]
        pc = _make_plane_client(existing_issues=existing)

        tid = svc._maybe_create_fix_validation_task(pc, task, "err", "run-1")
        assert tid == "new-task-123"
        pc.create_issue.assert_called_once()

    def test_dedup_in_review_state_is_open(self) -> None:
        svc = _make_service()
        task = _make_task("org/repo")
        existing = [
            {
                "id": "existing-1",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "In Review"},
            },
        ]
        pc = _make_plane_client(existing_issues=existing)

        tid = svc._maybe_create_fix_validation_task(pc, task, "err", "run-1")
        assert tid is None
        pc.create_issue.assert_not_called()

    def test_dedup_backlog_state_is_open(self) -> None:
        svc = _make_service()
        task = _make_task("org/repo")
        existing = [
            {
                "id": "existing-1",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "Backlog"},
            },
        ]
        pc = _make_plane_client(existing_issues=existing)

        tid = svc._maybe_create_fix_validation_task(pc, task, "err", "run-1")
        assert tid is None
        pc.create_issue.assert_not_called()

    def test_dedup_todo_state_is_open(self) -> None:
        svc = _make_service()
        task = _make_task("org/repo")
        existing = [
            {
                "id": "existing-1",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "Todo"},
            },
        ]
        pc = _make_plane_client(existing_issues=existing)

        tid = svc._maybe_create_fix_validation_task(pc, task, "err", "run-1")
        assert tid is None
        pc.create_issue.assert_not_called()

    def test_occurrence_count_includes_closed_issues(self) -> None:
        """Closed issues count toward the occurrence total."""
        svc = _make_service()
        task = _make_task("org/repo")
        existing = [
            {
                "id": "old-1",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "Done"},
            },
            {
                "id": "old-2",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "Cancelled"},
            },
        ]
        pc = _make_plane_client(existing_issues=existing)
        results = [_make_validation_result("lint", 1, stderr="bad")]

        svc._maybe_create_fix_validation_task(
            pc, task, "err", "run-1",
            validation_results=results,
        )

        call_kwargs = pc.create_issue.call_args[1]
        desc = call_kwargs["description"]
        # 2 existing + 1 new = occurrence #3
        assert "occurrence #3" in desc

    def test_fallback_description_without_validation_results(self) -> None:
        svc = _make_service()
        pc = _make_plane_client()
        task = _make_task()

        svc._maybe_create_fix_validation_task(pc, task, "old error text", "run-1")

        call_kwargs = pc.create_issue.call_args[1]
        desc = call_kwargs["description"]
        assert "## Validation Error" in desc
        assert "old error text" in desc
        assert "## Failing Commands" not in desc

    def test_returns_none_on_exception(self) -> None:
        svc = _make_service()
        pc = MagicMock()
        pc.list_issues.side_effect = RuntimeError("boom")
        task = _make_task()

        tid = svc._maybe_create_fix_validation_task(pc, task, "err", "run-1")
        assert tid is None


# ---------------------------------------------------------------------------
# Integration-level tests: baseline validation → fix-task creation
# ---------------------------------------------------------------------------


class TestBaselineValidationIntegration:
    """End-to-end flow through _run_baseline_validation → _maybe_create_fix_validation_task."""

    @staticmethod
    def _build_service() -> ExecutionService:
        svc = _make_service()
        svc.validation = MagicMock()
        svc.event_logger = MagicMock()
        return svc

    @staticmethod
    def _build_repo_target() -> MagicMock:
        rt = MagicMock()
        rt.repo_key = "org/repo"
        rt.validation_commands = ["ruff check", "pytest"]
        rt.validation_timeout_seconds = 300
        return rt

    def test_baseline_failure_creates_enriched_fix_task(self) -> None:
        # -- setup --
        svc = self._build_service()

        failing_result = _make_validation_result("ruff check", exit_code=1, stderr="lint error found")
        passing_result = _make_validation_result("pytest", exit_code=0)
        svc.validation.run.return_value = [failing_result, passing_result]
        svc.validation.passed.return_value = False

        repo_target = self._build_repo_target()
        repo_path = Path("/tmp/fake")
        run_env: dict[str, str] = {}
        run_id = "run-1"

        pc = _make_plane_client()  # list_issues=[], create_issue returns {"id": "new-task-123"}
        task = _make_task("org/repo")

        # -- exercise baseline validation --
        baseline = svc._run_baseline_validation(repo_target, repo_path, run_env, run_id)

        assert baseline.failed is True
        assert baseline.error_text is not None
        assert baseline.validation_results is not None
        assert len(baseline.validation_results) == 2

        # -- exercise fix-task creation --
        tid = svc._maybe_create_fix_validation_task(
            pc, task, baseline.error_text, run_id,
            validation_results=baseline.validation_results,
            repo_target=repo_target,
        )

        assert tid == "new-task-123"
        pc.create_issue.assert_called_once()

        call_kwargs = pc.create_issue.call_args[1]
        desc = call_kwargs["description"]
        assert "ruff check" in desc
        assert "exit code: 1" in desc
        assert "lint error found" in desc
        assert "occurrence #1" in desc

    def test_dedup_prevents_second_creation(self) -> None:
        # -- setup --
        svc = self._build_service()

        failing_result = _make_validation_result("ruff check", exit_code=1, stderr="lint error found")
        passing_result = _make_validation_result("pytest", exit_code=0)
        svc.validation.run.return_value = [failing_result, passing_result]
        svc.validation.passed.return_value = False

        repo_target = self._build_repo_target()
        repo_path = Path("/tmp/fake")
        run_env: dict[str, str] = {}
        run_id = "run-1"

        task = _make_task("org/repo")

        # First call — no existing issues, creates a task
        pc = _make_plane_client()
        baseline = svc._run_baseline_validation(repo_target, repo_path, run_env, run_id)
        tid1 = svc._maybe_create_fix_validation_task(
            pc, task, baseline.error_text, run_id,
            validation_results=baseline.validation_results,
            repo_target=repo_target,
        )
        assert tid1 == "new-task-123"
        pc.create_issue.assert_called_once()

        # Second call — an open issue already exists, should be deduped
        pc.create_issue.reset_mock()
        pc.list_issues.return_value = [
            {
                "id": "new-task-123",
                "name": "Fix pre-existing validation failure in org/repo",
                "state": {"name": "In Progress"},
            },
        ]

        tid2 = svc._maybe_create_fix_validation_task(
            pc, task, baseline.error_text, run_id,
            validation_results=baseline.validation_results,
            repo_target=repo_target,
        )
        assert tid2 is None
        pc.create_issue.assert_not_called()
