"""Tests for the policy retry pass in ExecutionService (service.py lines 539-565).

Covers:
1. Policy violations trigger retry, successful retry clears violations → Review
2. Failed retry writes policy_violations artifact → Blocked
3. Tasks without allowed_paths skip retry entirely
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_plane.legacy_execution.service import ExecutionService
from control_plane.config.settings import Settings


class PolicyPlaneClient:
    """PlaneClient that returns a task description with allowed_paths."""

    def __init__(self, allowed_paths: list[str]) -> None:
        self._allowed_paths = allowed_paths

    def _description(self) -> str:
        lines = [
            "## Execution",
            "repo: repo_a",
            "base_branch: main",
            "mode: goal",
        ]
        if self._allowed_paths:
            lines.append("allowed_paths:")
            for p in self._allowed_paths:
                lines.append(f"  - {p}")
        lines.extend(["", "## Goal", "Do thing."])
        return "\n".join(lines) + "\n"

    def fetch_issue(self, task_id: str) -> dict[str, object]:
        return {
            "id": task_id,
            "name": "Task",
            "project_id": "proj",
            "description": self._description(),
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


class TrackingPlaneClient(PolicyPlaneClient):
    """Extends PolicyPlaneClient with transition/comment tracking."""

    def __init__(self, allowed_paths: list[str]) -> None:
        super().__init__(allowed_paths)
        self.transitions: list[str] = []
        self.comments: list[str] = []

    def transition_issue(self, task_id: str, state: str) -> None:  # noqa: ARG002
        self.transitions.append(state)

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:  # noqa: ARG002
        self.comments.append(comment_markdown)


@pytest.fixture
def base_settings(tmp_path: Path) -> Settings:
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


def _wire_service(
    service: ExecutionService,
    tmp_path: Path,
    kodo_run_fn,  # type: ignore[no-untyped-def]
    changed_files_fn,  # type: ignore[no-untyped-def]
) -> Path:
    """Wire all service dependencies with stubs. Returns workspace path."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir(exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)

    service.workspace.create = lambda: workspace  # type: ignore[assignment]
    service.workspace.cleanup = lambda path: None  # type: ignore[assignment]
    service.git.clone = lambda clone_url, workspace_path: repo_path  # type: ignore[assignment]
    service.git.add_local_exclude = lambda repo_path, pattern: None  # type: ignore[assignment]
    service.git.verify_remote_branch_exists = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.checkout_base = lambda repo_path, branch: None  # type: ignore[assignment]
    service.git.set_identity = lambda repo_path, author_name, author_email: None  # type: ignore[assignment]
    service.git.create_task_branch = lambda repo_path, task_branch: None  # type: ignore[assignment]
    service.git.changed_files = changed_files_fn  # type: ignore[assignment]
    service.git.diff_stat = lambda repo_path: "src/a.py | 2 +-"  # type: ignore[assignment]
    service.git.diff_patch = lambda repo_path: "diff --git a/src/a.py b/src/a.py"  # type: ignore[assignment]
    service.git.commit_all = lambda repo_path, message: True  # type: ignore[assignment]
    service.git.push_branch = lambda repo_path, branch: None  # type: ignore[assignment]

    # write_goal_file must create a real file so the policy retry can read/rewrite it
    def write_goal_file(path: Path, goal_text: str, constraints_text: str | None = None) -> Path:
        lines = [f"## Goal\n{goal_text}"]
        if constraints_text:
            lines.append(f"\n## Constraints\n{constraints_text}")
        path.write_text("\n".join(lines) + "\n")
        return path

    service.kodo.write_goal_file = write_goal_file  # type: ignore[assignment]
    service.kodo.run = kodo_run_fn  # type: ignore[assignment]
    service.kodo.command_to_json = lambda cmd: "{}"  # type: ignore[assignment]
    service.kodo.is_orchestrator_rate_limited = lambda result: False  # type: ignore[assignment]
    service.validation.run = lambda commands, cwd, env=None, **kwargs: []  # type: ignore[assignment]
    service.validation.passed = lambda results: True  # type: ignore[assignment]
    service.bootstrapper.prepare = lambda *args, **kwargs: type("BootstrapResult", (), {"env": {}, "commands": []})()  # type: ignore[assignment]

    return workspace


def test_policy_retry_succeeds_clears_violations_routes_to_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_settings: Settings
) -> None:
    """Scenarios 1 & 2: Policy violations trigger retry. After retry, violations cleared → Review."""
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    service = ExecutionService(base_settings)

    kodo_call_count = 0

    def kodo_run(goal_file, repo_path, env=None, profile=None, kodo_mode="goal"):  # type: ignore[no-untyped-def]
        nonlocal kodo_call_count
        kodo_call_count += 1
        return type("KodoResult", (), {"exit_code": 0, "stdout": f"run{kodo_call_count}", "stderr": "", "command": ["kodo"]})()

    changed_files_call_count = 0

    def changed_files_fn(repo_path):  # type: ignore[no-untyped-def]
        nonlocal changed_files_call_count
        changed_files_call_count += 1
        if changed_files_call_count == 1:
            # First call (before policy retry): out-of-scope file present
            return ["src/a.py", "deploy/config.yml"]
        # After policy retry: violation reverted
        return ["src/a.py"]

    workspace = _wire_service(service, tmp_path, kodo_run, changed_files_fn)
    client = TrackingPlaneClient(allowed_paths=["src/"])

    result = service.run_task(client, "TASK-POL1", preauthorized=True)

    # kodo.run called twice: initial + policy retry
    assert kodo_call_count == 2

    # Goal file was appended with the scope constraint violation message
    goal_file = workspace / "goal.md"
    goal_content = goal_file.read_text()
    assert "Scope Constraint Violation" in goal_content
    assert "deploy/config.yml" in goal_content
    assert "Allowed paths:" in goal_content

    # Result routes to Review with no violations
    assert result.final_status == "Review"
    assert result.success is True
    assert result.policy_violations == []

    # Transition ends at Review
    assert client.transitions[-1] == "Review"


def test_policy_retry_fails_writes_artifact_routes_to_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_settings: Settings
) -> None:
    """Scenario 3: Retry still has violations → Blocked + policy_violation artifact."""
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    service = ExecutionService(base_settings)

    kodo_call_count = 0

    def kodo_run(goal_file, repo_path, env=None, profile=None, kodo_mode="goal"):  # type: ignore[no-untyped-def]
        nonlocal kodo_call_count
        kodo_call_count += 1
        return type("KodoResult", (), {"exit_code": 0, "stdout": f"run{kodo_call_count}", "stderr": "", "command": ["kodo"]})()

    # Both before and after retry, out-of-scope file remains
    def changed_files_fn(repo_path):  # type: ignore[no-untyped-def]
        return ["src/a.py", "deploy/config.yml"]

    _wire_service(service, tmp_path, kodo_run, changed_files_fn)
    client = TrackingPlaneClient(allowed_paths=["src/"])

    result = service.run_task(client, "TASK-POL2", preauthorized=True)

    # kodo.run called twice: initial + policy retry
    assert kodo_call_count == 2

    # Routes to Blocked with remaining violations
    assert result.final_status == "Blocked"
    assert result.policy_violations == ["deploy/config.yml"]
    assert result.success is False

    # policy_violation.json artifact exists in the run directory
    report_root = tmp_path / "reports"
    run_dirs = list(report_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    violation_file = run_dir / "policy_violation.json"
    assert violation_file.exists()
    violation_data = json.loads(violation_file.read_text())
    assert "deploy/config.yml" in violation_data["violations"]

    # Verify the artifact path is in result.artifacts
    assert any("policy_violation.json" in a for a in result.artifacts)


def test_no_allowed_paths_skips_policy_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_settings: Settings
) -> None:
    """Scenario 4: No allowed_paths → no retry, no violations."""
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    service = ExecutionService(base_settings)

    kodo_call_count = 0

    def kodo_run(goal_file, repo_path, env=None, profile=None, kodo_mode="goal"):  # type: ignore[no-untyped-def]
        nonlocal kodo_call_count
        kodo_call_count += 1
        return type("KodoResult", (), {"exit_code": 0, "stdout": f"run{kodo_call_count}", "stderr": "", "command": ["kodo"]})()

    # Changes include files outside src/, but no allowed_paths so no policy check
    def changed_files_fn(repo_path):  # type: ignore[no-untyped-def]
        return ["src/a.py", "deploy/config.yml"]

    _wire_service(service, tmp_path, kodo_run, changed_files_fn)
    # Empty allowed_paths — policy retry should be skipped
    client = TrackingPlaneClient(allowed_paths=[])

    result = service.run_task(client, "TASK-POL3", preauthorized=True)

    # kodo.run called only once — no retry
    assert kodo_call_count == 1

    # No policy violations (find_violations returns [] when allowed_paths is empty)
    assert result.policy_violations == []

    # Routes to Review (success)
    assert result.final_status == "Review"
    assert result.success is True
