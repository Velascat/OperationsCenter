"""Tests for _maybe_create_fix_validation_task enriched descriptions."""

from __future__ import annotations

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
