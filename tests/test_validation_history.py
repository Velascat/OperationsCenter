"""Tests for ValidationHistory: signature computation, recording, recurring detection, and service integration."""

from __future__ import annotations

import json
from pathlib import Path

from control_plane.application.service import ExecutionService
from control_plane.application.validation_history import ValidationHistory
from control_plane.config.settings import Settings
from control_plane.domain.models import BoardTask, ValidationResult
from control_plane.execution.models import NoOpDecision, RetryDecision


# ---------------------------------------------------------------------------
# Unit tests for ValidationHistory
# ---------------------------------------------------------------------------


class TestComputeSignatures:
    def test_returns_command_and_hash(self) -> None:
        """compute_signatures produces (command, md5_hex) for failed results."""
        results = [
            ValidationResult(command="ruff check .", exit_code=1, stdout="", stderr="  Error: E501  \n", duration_ms=1),
        ]
        sigs = ValidationHistory.compute_signatures(results)
        assert len(sigs) == 1
        cmd, digest = sigs[0]
        assert cmd == "ruff check ."
        assert len(digest) == 32  # md5 hex

        # Verify normalization: same text with different whitespace/case -> same hash
        results2 = [
            ValidationResult(command="ruff check .", exit_code=1, stdout="", stderr="error: e501", duration_ms=1),
        ]
        sigs2 = ValidationHistory.compute_signatures(results2)
        assert sigs2[0][1] == sigs[0][1]

    def test_skips_passing(self) -> None:
        """Passing results (exit_code=0) are excluded from signatures."""
        results = [
            ValidationResult(command="pytest -q", exit_code=0, stdout="ok", stderr="", duration_ms=1),
            ValidationResult(command="ruff check .", exit_code=1, stdout="", stderr="err", duration_ms=1),
        ]
        sigs = ValidationHistory.compute_signatures(results)
        assert len(sigs) == 1
        assert sigs[0][0] == "ruff check ."


class TestRecordAndCheckRecurring:
    def _make_run_dir(self, report_root: Path, task_id: str, run_index: int) -> Path:
        """Create a fake run directory following the Reporter naming convention."""
        ts = f"2026-04-03T00:00:{run_index:02d}"
        run_dir = report_root / f"{ts}_{task_id}_run{run_index}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def test_record_and_check_recurring(self, tmp_path: Path) -> None:
        """When the same signature appears >= threshold times, check_recurring returns True."""
        report_root = tmp_path / "reports"
        task_id = "TASK-1"
        sigs = [("ruff check .", "abc123")]

        # Create 3 past run dirs with the same signature
        for i in range(3):
            d = self._make_run_dir(report_root, task_id, i)
            (d / "validation_signatures.json").write_text(json.dumps(sigs))

        history = ValidationHistory(report_root)
        assert history.check_recurring(task_id, sigs, window=5, threshold=2) is True

    def test_check_recurring_below_threshold(self, tmp_path: Path) -> None:
        """Returns False when signature count is below threshold."""
        report_root = tmp_path / "reports"
        task_id = "TASK-2"
        sigs = [("ruff check .", "abc123")]

        # Only 1 past run with this signature (threshold=2 by default)
        d = self._make_run_dir(report_root, task_id, 0)
        (d / "validation_signatures.json").write_text(json.dumps(sigs))

        history = ValidationHistory(report_root)
        assert history.check_recurring(task_id, sigs, window=5, threshold=2) is False

    def test_record_signatures_writes_file(self, tmp_path: Path) -> None:
        """record_signatures creates validation_signatures.json in the latest run dir."""
        report_root = tmp_path / "reports"
        task_id = "TASK-3"
        run_dir = self._make_run_dir(report_root, task_id, 0)
        sigs = [("pytest -q", "deadbeef")]

        history = ValidationHistory(report_root)
        history.record_signatures(task_id, sigs)

        sig_file = run_dir / "validation_signatures.json"
        assert sig_file.exists()
        assert json.loads(sig_file.read_text()) == [list(s) for s in sigs]

    def test_check_recurring_no_dirs(self, tmp_path: Path) -> None:
        """Returns False when there are no run directories."""
        history = ValidationHistory(tmp_path / "nonexistent")
        assert history.check_recurring("TASK-X", [("cmd", "hash")]) is False

    def test_check_recurring_empty_signatures(self, tmp_path: Path) -> None:
        """Empty signature list short-circuits to False without scanning run dirs."""
        report_root = tmp_path / "reports"
        task_id = "TASK-EMPTY"

        # Create run dirs with signatures — would match if scanned
        for i in range(3):
            d = self._make_run_dir(report_root, task_id, i)
            (d / "validation_signatures.json").write_text(json.dumps([("cmd", "hash")]))

        history = ValidationHistory(report_root)
        assert history.check_recurring(task_id, [], window=5, threshold=1) is False


# ---------------------------------------------------------------------------
# Integration test: service skips retry on recurring failure
# ---------------------------------------------------------------------------


class DummyPlaneClient:
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
                    "clone_url": "git@github.com:x/repo_a.git",
                    "default_branch": "main",
                    "validation_commands": ["pytest -q"],
                }
            },
            "report_root": str(tmp_path / "reports"),
        }
    )


def _make_task() -> BoardTask:
    return BoardTask(
        task_id="TASK-REC",
        project_id="proj",
        title="Recurring",
        description="desc",
        status="Ready for AI",
        repo_key="repo_a",
        base_branch="main",
        execution_mode="goal",
        goal_text="Do thing.",
    )


def test_skip_retry_on_recurring_failure(tmp_path: Path) -> None:
    """When history shows recurring failure pattern, retry is skipped."""
    settings = _make_settings(tmp_path)
    service = ExecutionService(settings)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

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

    # Validation always fails with the same error
    call_count = 0

    def failing_validation(commands, cwd, env=None, **kwargs):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return [
            ValidationResult(command=cmd, exit_code=1, stdout="", stderr="some error", duration_ms=1)
            for cmd in commands
        ]

    service.validation.run = failing_validation  # type: ignore[assignment]

    # Seed history: create 2 past run dirs with the same failure signatures
    report_root = Path(settings.report_root)
    task_id = "TASK-REC"
    sigs = ValidationHistory.compute_signatures(
        [ValidationResult(command="pytest -q", exit_code=1, stdout="", stderr="some error", duration_ms=1)]
    )
    for i in range(2):
        ts = f"2026-04-03T00:00:{i:02d}"
        d = report_root / f"{ts}_{task_id}_run{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "validation_signatures.json").write_text(json.dumps(sigs))

    result = service.run_task(DummyPlaneClient(_make_task()), task_id)

    # Retry should have been skipped: validation.run called only ONCE (initial)
    assert call_count == 1
    assert result.validation_passed is False
    assert result.validation_retried is False
    assert result.outcome_reason == "recurring_validation_failure"


def test_first_failure_records_signatures(tmp_path: Path) -> None:
    """After a single failed run with no prior history, validation_signatures.json is created."""
    settings = _make_settings(tmp_path)
    service = ExecutionService(settings)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

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
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    goal_file = workspace_dir / "goal.md"
    goal_file.write_text("Do thing.")

    service.kodo.write_goal_file = lambda path, goal_text, constraints_text: goal_file  # type: ignore[assignment]
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

    # Validation always fails — both initial and retry
    def failing_validation(commands, cwd, env=None, **kwargs):  # noqa: ARG001
        return [
            ValidationResult(command=cmd, exit_code=1, stdout="", stderr="first fail", duration_ms=1)
            for cmd in commands
        ]

    service.validation.run = failing_validation  # type: ignore[assignment]

    # No prior history — this is the very first run
    result = service.run_task(DummyPlaneClient(_make_task()), "TASK-REC")

    assert result.validation_passed is False

    # Verify that validation_signatures.json was created in the run directory
    report_root = Path(settings.report_root)
    sig_files = list(report_root.glob("*TASK-REC*/validation_signatures.json"))
    assert len(sig_files) >= 1, "Expected validation_signatures.json to be created on first failure"
    # Verify the file contains valid signature data
    content = json.loads(sig_files[0].read_text())
    assert isinstance(content, list)
    assert len(content) > 0


def test_recurring_failure_skips_retry_e2e(tmp_path: Path) -> None:
    """End-to-end: two consecutive runs with identical validation failures.

    Run 1 → fails validation, records signatures (no recurring history yet, so retry happens).
    Run 2 → fails with same output, detects recurring pattern, skips retry,
             sets outcome_reason='recurring_validation_failure'.
    """
    settings = _make_settings(tmp_path)
    # With threshold=1, a single prior run with matching signatures triggers detection.
    settings.recurring_failure_threshold = 1
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    validation_call_counts: list[int] = []

    def _run_once(run_index: int) -> object:
        """Execute one service.run_task cycle, return the result."""
        service = ExecutionService(settings)

        service.workspace.create = lambda: tmp_path / f"workspace_{run_index}"  # type: ignore[assignment]
        (tmp_path / f"workspace_{run_index}").mkdir(exist_ok=True)
        service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
        service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
        service.git.add_local_exclude = lambda repo_path, pattern: None  # type: ignore[assignment]
        service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
        service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
        service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
        service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
        service.git.changed_files = lambda repo_path: []  # type: ignore[assignment]
        service.git.commit_all = lambda repo_path, message: False  # type: ignore[assignment]

        goal_file = tmp_path / f"workspace_{run_index}" / "goal.md"
        goal_file.write_text("Do thing.")
        service.kodo.write_goal_file = lambda path, goal_text, constraints_text: goal_file  # type: ignore[assignment]
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

        call_count = 0

        def failing_validation(commands, cwd, env=None, **kwargs):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            return [
                ValidationResult(command=cmd, exit_code=1, stdout="", stderr="error: E501 line too long", duration_ms=1)
                for cmd in commands
            ]

        service.validation.run = failing_validation  # type: ignore[assignment]

        result = service.run_task(DummyPlaneClient(_make_task()), "TASK-REC")
        validation_call_counts.append(call_count)
        return result

    # Run 1: first failure — no recurring history, so retry happens (validation called twice)
    result1 = _run_once(0)
    assert result1.validation_passed is False
    assert validation_call_counts[0] == 2  # initial + retry

    # Run 2: same failure — recurring pattern detected, retry skipped (validation called once)
    result2 = _run_once(1)
    assert result2.validation_passed is False
    assert result2.validation_retried is False
    assert result2.outcome_reason == "recurring_validation_failure"
    assert validation_call_counts[1] == 1  # only initial, no retry
