import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from control_plane.execution import ExecutionControlSettings, UsageStore
from control_plane.domain.models import ExecutionResult
from control_plane.entrypoints.worker.main import (
    ProposalSpec,
    UNBLOCK_COMMENT_MARKER,
    UNKNOWN_BLOCKED_CLASSIFICATION,
    blocked_resolution_is_complete,
    build_improve_triage_result,
    classify_blocked_issue,
    extract_triage_follow_up_ids,
    handle_goal_task,
    handle_improve_task,
    handle_propose_cycle,
    handle_blocked_triage,
    handle_test_task,
    issue_status_name,
    issue_task_kind,
    recently_proposed,
    record_proposed,
    reconcile_stale_running_issues,
    run_watch_loop,
    select_ready_task_id,
    select_watch_candidate,
)


class FakePlaneClient:
    def __init__(
        self,
        issues: list[dict[str, object]],
        comments: dict[str, list[dict[str, object]]] | None = None,
    ) -> None:
        self._issues = issues
        self._comments = comments or {}
        self.transitions: list[tuple[str, str]] = []
        self.created: list[dict[str, object]] = []
        self.issue_comments: list[tuple[str, str]] = []

    def list_issues(self) -> list[dict[str, object]]:
        return self._issues

    def fetch_issue(self, task_id: str) -> dict[str, object]:
        for issue in self._issues:
            if issue["id"] == task_id:
                return issue
        raise KeyError(task_id)

    def transition_issue(self, task_id: str, state: str) -> None:
        self.transitions.append((task_id, state))
        for issue in self._issues:
            if issue["id"] == task_id:
                issue["state"] = {"name": state}

    def list_comments(self, task_id: str) -> list[dict[str, object]]:
        return self._comments.get(task_id, [])

    def create_issue(
        self,
        *,
        name: str,
        description: str,
        state: str | None = None,
        label_names: list[str] | None = None,
    ) -> dict[str, object]:
        issue = {
            "id": f"FOLLOWUP-{len(self.created) + 1}",
            "name": name,
            "description": description,
            "state": {"name": state or "Backlog"},
            "labels": [{"name": label} for label in (label_names or [])],
        }
        self.created.append(issue)
        self._issues.append(issue)
        return issue

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        self.issue_comments.append((task_id, comment_markdown))
        self._comments.setdefault(task_id, []).append({"comment_html": f"<p>{comment_markdown}</p>"})


class FakeService:
    def __init__(self, *, success: bool = True) -> None:
        usage_path = Path(tempfile.mkdtemp()) / "usage.json"
        settings = SimpleNamespace(
            repos={
                "control-plane": SimpleNamespace(
                    default_branch="main",
                    clone_url="git@github.com:Velascat/ControlPlane.git",
                    allowed_base_branches=["main"],
                )
            },
            report_root="tools/report/kodo_plane",
        )
        settings.git_token = lambda: None
        settings.execution_controls = lambda: ExecutionControlSettings(
            max_exec_per_hour=max(0, int(os.environ.get("CONTROL_PLANE_MAX_EXEC_PER_HOUR", "10"))),
            max_exec_per_day=max(0, int(os.environ.get("CONTROL_PLANE_MAX_EXEC_PER_DAY", "50"))),
            max_retries_per_task=max(1, int(os.environ.get("CONTROL_PLANE_MAX_RETRIES_PER_TASK", "3"))),
            min_watch_interval_seconds=1,
            min_remaining_exec_for_proposals=max(
                0,
                int(os.environ.get("CONTROL_PLANE_MIN_REMAINING_EXEC_FOR_PROPOSALS", "3")),
            ),
            usage_path=usage_path,
        )
        self.runs: list[str] = []
        self.settings = settings
        self._success = success
        self.usage_store = UsageStore(usage_path)

    def run_task(self, client: FakePlaneClient, task_id: str) -> ExecutionResult:  # noqa: ARG002
        self.runs.append(task_id)
        return ExecutionResult(
            run_id="run-123",
            success=self._success,
            changed_files=[],
            validation_passed=self._success,
            validation_results=[],
            branch_pushed=False,
            draft_branch_pushed=False,
            push_reason=None,
            pull_request_url=None,
            summary="ok" if self._success else "failed",
            artifacts=[],
            policy_violations=[],
        )


def test_issue_status_name_prefers_state_name() -> None:
    assert issue_status_name({"state": {"name": "Ready for AI"}}) == "Ready for AI"


class FakeUsageStore(UsageStore):
    """UsageStore backed by in-memory task_attempts and task_signatures dicts."""

    def __init__(
        self,
        *,
        task_attempts: dict[str, int] | None = None,
        task_signatures: dict[str, str] | None = None,
    ) -> None:
        import tempfile
        super().__init__(Path(tempfile.mkdtemp()) / "usage.json")
        self._fake_attempts = task_attempts or {}
        self._fake_signatures = task_signatures or {}

    def load(self) -> dict:  # type: ignore[override]
        return {
            "task_attempts": dict(self._fake_attempts),
            "last_task_signatures": dict(self._fake_signatures),
            "events": [],
        }


def test_select_ready_task_id_returns_first_matching_issue() -> None:
    client = FakePlaneClient(
        [
            {"id": "TASK-1", "state": {"name": "Backlog"}},
            {"id": "TASK-2", "state": {"name": "Ready for AI"}},
        ]
    )

    assert select_ready_task_id(client) == "TASK-2"


def test_select_watch_candidate_fetches_detail_when_labels_missing_for_improve_role() -> None:
    class DetailedClient(FakePlaneClient):
        def fetch_issue(self, task_id: str) -> dict[str, object]:
            if task_id == "IMPROVE-1":
                return {
                    "id": "IMPROVE-1",
                    "state": {"name": "Ready for AI"},
                    "labels": [{"name": "task-kind: improve"}],
                }
            return super().fetch_issue(task_id)

    client = DetailedClient(
        [
            {"id": "IMPROVE-1", "state": {"name": "Ready for AI"}, "labels": []},
        ]
    )

    task_id, action = select_watch_candidate(client, ready_state="Ready for AI", role="improve")

    assert (task_id, action) == ("IMPROVE-1", "improve_task")


def test_issue_task_kind_defaults_to_goal_without_label() -> None:
    assert issue_task_kind({"labels": []}) == "goal"


def test_issue_task_kind_extracts_label_value() -> None:
    assert issue_task_kind({"labels": [{"name": "task-kind: test"}]}) == "test"


def test_classify_blocked_issue_detects_validation_failure() -> None:
    classification, rationale = classify_blocked_issue(
        {"name": "Task"},
        [{"comment_html": "<p>[Goal] Execution result</p><ul><li>validation_passed: False</li></ul>"}],
    )
    assert classification == "validation_failure"
    assert "Validation failed" in rationale


def test_classify_blocked_issue_detects_provider_auth_failure() -> None:
    classification, rationale = classify_blocked_issue(
        {"name": "Task"},
        [{"comment_html": "<p>[Goal] Execution result</p><ul><li>execution_stderr: Error: ANTHROPIC_API_KEY not set</li></ul>"}],
    )
    assert classification == "infra_tooling"
    assert "tooling" in rationale.lower()


def test_classify_blocked_issue_detects_verification_failure_for_test_tasks() -> None:
    classification, rationale = classify_blocked_issue(
        {"name": "Task", "labels": [{"name": "task-kind: test"}]},
        [{"comment_html": "<p>[Test] Execution result</p><ul><li>validation_passed: False</li></ul>"}],
    )
    assert classification == "verification_failure"
    assert "Verification failed" in rationale


def test_classify_blocked_issue_no_false_positive_on_configured() -> None:
    classification, _ = classify_blocked_issue(
        {"name": "Task", "description": "The configured validation commands ran successfully"},
        [{"comment_html": "<p>We verified this in a properly configured environment.</p>"}],
    )

    assert classification == UNKNOWN_BLOCKED_CLASSIFICATION


def test_classify_blocked_issue_no_false_positive_on_parse() -> None:
    classification, _ = classify_blocked_issue(
        {"name": "Task", "description": "Failed to parse the input data correctly"},
        [{"comment_html": "<p>We need to parse the output format.</p>"}],
    )

    assert classification == UNKNOWN_BLOCKED_CLASSIFICATION


def test_classify_blocked_issue_true_positive_taskcontracterror() -> None:
    classification, _ = classify_blocked_issue(
        {"name": "Task"},
        [{"comment_html": "<p>TaskContractError: Unknown repo key 'my-repo'</p>"}],
    )
    assert classification == "parse_config"

    classification, _ = classify_blocked_issue(
        {"name": "Task"},
        [{"comment_html": "<p>Missing execution metadata in task contract</p>"}],
    )
    assert classification == "parse_config"


def test_classify_blocked_issue_no_false_positive_code_youtube_shorts_scenario() -> None:
    classification, _ = classify_blocked_issue(
        {
            "name": "code_youtube_shorts: Fix validation errors",
            "description": "The configured validation commands are failing",
        },
        [{"comment_html": "<p>Need to parse and fix the validation output.</p>"}],
    )

    assert classification == UNKNOWN_BLOCKED_CLASSIFICATION


def test_run_watch_loop_claims_and_runs_one_goal_task(caplog: pytest.LogCaptureFixture) -> None:
    client = FakePlaneClient(
        [
            {"id": "TASK-1", "state": {"name": "Ready for AI"}, "labels": [{"name": "task-kind: goal"}]},
        ]
    )
    service = FakeService()

    with caplog.at_level(logging.INFO):
        run_watch_loop(
            client,
            service,
            role="goal",
            ready_state="Ready for AI",
            poll_interval_seconds=0,
            max_cycles=1,
        )

    assert client.transitions == [("TASK-1", "Running")]
    assert service.runs == ["TASK-1"]


def test_run_watch_loop_test_role_creates_follow_up_goal_task_on_failure() -> None:
    client = FakePlaneClient(
        [
            {"id": "TEST-1", "name": "Verify watcher", "state": {"name": "Ready for AI"}, "labels": [{"name": "task-kind: test"}]},
        ]
    )
    service = FakeService(success=False)

    run_watch_loop(
        client,
        service,
        role="test",
        ready_state="Ready for AI",
        poll_interval_seconds=0,
        max_cycles=1,
    )

    assert service.runs == ["TEST-1"]
    assert client.created
    assert client.created[0]["labels"] == [
        {"name": "task-kind: goal"},
        {"name": "source: test-worker"},
    ]


def test_run_watch_loop_goal_blocks_after_retry_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("CONTROL_PLANE_MAX_RETRIES_PER_TASK", "1")
    client = FakePlaneClient(
        [
            {"id": "TASK-RETRY", "name": "Retry me", "state": {"name": "Ready for AI"}, "labels": [{"name": "task-kind: goal"}]},
        ]
    )
    service = FakeService()
    service.usage_store.record_execution(  # type: ignore[attr-defined]
        role="goal",
        task_id="TASK-RETRY",
        signature="prior",
        now=datetime.now(UTC) - timedelta(minutes=5),  # recent — within the 1h auto-reset window
    )

    run_watch_loop(
        client,
        service,
        role="goal",
        ready_state="Ready for AI",
        poll_interval_seconds=0,
        max_cycles=1,
    )

    assert service.runs == []
    assert ("TASK-RETRY", "Blocked") in client.transitions
    assert any("retry cap" in comment.lower() for _, comment in client.issue_comments)


def test_run_watch_loop_improve_role_triages_blocked_task() -> None:
    client = FakePlaneClient(
        [
            {"id": "BLOCKED-1", "name": "Broken task", "state": {"name": "Blocked"}, "labels": [{"name": "task-kind: goal"}]},
        ],
        comments={
            "BLOCKED-1": [
                {"comment_html": "<p>[Goal] Execution result</p><ul><li>validation_passed: False</li></ul>"},
            ]
        },
    )
    service = FakeService()

    run_watch_loop(
        client,
        service,
        role="improve",
        ready_state="Ready for AI",
        poll_interval_seconds=0,
        max_cycles=1,
    )

    assert service.runs == []
    assert client.created
    assert client.created[0]["labels"] == [
        {"name": "task-kind: goal"},
        {"name": "source: improve-worker"},
    ]
    assert any("[Improve] Blocked triage" in comment for _, comment in client.issue_comments)


def test_handle_goal_task_hands_off_blocked_work_to_improve_triage() -> None:
    client = FakePlaneClient(
        [
            {
                "id": "GOAL-1",
                "name": "Fix watcher",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ]
    )
    service = FakeService(success=False)
    service.run_task = lambda _client, task_id: ExecutionResult(  # type: ignore[assignment]
        run_id="run-123",
        success=False,
        changed_files=[],
        validation_passed=True,
        validation_results=[],
        branch_pushed=False,
        draft_branch_pushed=False,
        push_reason=None,
        pull_request_url=None,
        execution_stderr_excerpt=None,
        summary="failed without changes",
        artifacts=[],
        policy_violations=[],
    )

    created_ids = handle_goal_task(client, service, "GOAL-1")

    assert created_ids == []
    assert client.created == []
    assert any("[Goal] Blocked; handed off to Improve triage" in comment for _, comment in client.issue_comments)


def test_handle_goal_task_creates_test_follow_up_on_success_with_changes() -> None:
    client = FakePlaneClient(
        [
            {
                "id": "GOAL-3",
                "name": "Implement watcher fix",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ]
    )
    service = FakeService(success=True)
    service.run_task = lambda _client, task_id: ExecutionResult(  # type: ignore[assignment]
        run_id="run-125",
        success=True,
        changed_files=["src/control_plane/entrypoints/worker/main.py"],
        validation_passed=True,
        validation_results=[],
        branch_pushed=True,
        draft_branch_pushed=False,
        push_reason="success",
        pull_request_url=None,
        execution_stderr_excerpt=None,
        summary="success with changes",
        artifacts=[],
        policy_violations=[],
        final_status="Review",
    )

    created_ids = handle_goal_task(client, service, "GOAL-3")

    assert created_ids == ["FOLLOWUP-1"]
    assert client.created[0]["labels"] == [
        {"name": "task-kind: test"},
        {"name": "source: goal-worker"},
    ]
    assert any("[Goal] Execution complete; handed off to Test" in comment for _, comment in client.issue_comments)


def test_handle_goal_task_does_not_create_follow_up_for_provider_auth_failure() -> None:
    client = FakePlaneClient(
        [
            {
                "id": "GOAL-2",
                "name": "Fix watcher",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ]
    )
    service = FakeService(success=False)
    service.run_task = lambda _client, task_id: ExecutionResult(  # type: ignore[assignment]
        run_id="run-124",
        success=False,
        changed_files=[],
        validation_passed=True,
        validation_results=[],
        branch_pushed=False,
        draft_branch_pushed=False,
        push_reason=None,
        pull_request_url=None,
        execution_stderr_excerpt="Error: ANTHROPIC_API_KEY not set",
        summary="failed without changes",
        artifacts=[],
        policy_violations=[],
    )

    created_ids = handle_goal_task(client, service, "GOAL-2")

    assert created_ids == []
    assert client.created == []
    assert any("[Goal] Execution blocked by environment/auth" in comment for _, comment in client.issue_comments)


def test_handle_goal_task_does_not_create_test_follow_up_for_internal_only_no_op() -> None:
    client = FakePlaneClient(
        [
            {
                "id": "GOAL-NOOP",
                "name": "Implement watcher fix",
                "description": "## Execution\nrepo: ControlPlane\nbase_branch: main\nmode: goal\n\n## Evidence\n- recent commits: abc123 Fix prompt wiring\n- recently changed files: test/conftest.py, src/main.py",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ]
    )
    service = FakeService(success=True)
    service.run_task = lambda _client, task_id: ExecutionResult(  # type: ignore[assignment]
        run_id="run-noop-goal",
        success=True,
        outcome_status="no_op",
        outcome_reason="internal_only_change",
        changed_files=[],
        internal_changed_files=["kodo/config.json"],
        validation_passed=True,
        validation_results=[],
        branch_pushed=False,
        draft_branch_pushed=False,
        push_reason=None,
        pull_request_url=None,
        execution_stderr_excerpt=None,
        summary="run_id=run-noop-goal execution=passed validation=passed policy=passed branch_push=not_pushed changed_files=0 internal_changed_files=1 no_op=true",
        artifacts=[],
        policy_violations=[],
        final_status="Blocked",
    )

    created_ids = handle_goal_task(client, service, "GOAL-NOOP")

    assert created_ids == []
    assert client.created == []
    assert any("No meaningful repo change produced" in comment for _, comment in client.issue_comments)
    assert any("selected_evidence: recent commits: abc123 Fix prompt wiring" in comment for _, comment in client.issue_comments)
    assert any("target_area_hint: test/conftest.py, src/main.py" in comment for _, comment in client.issue_comments)


def test_handle_improve_task_discovers_repo_follow_ups(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient(
        [
            {
                "id": "IMPROVE-1",
                "name": "Inspect repo",
                "description": "## Execution\nrepo: control-plane\nbase_branch: main\nmode: improve\n\n## Goal\nInspect repo",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: improve"}],
            },
        ]
    )
    service = FakeService()

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.discover_improvement_candidates",
        lambda _service, *, repo_key, base_branch=None: (
            [
                {
                    "kind": "goal",
                    "title": "Harden worker classification",
                    "goal": "Improve worker failure classification.",
                    "constraints": "- keep the change bounded",
                    "note": "- note: found in reports",
                },
                {
                    "kind": "test",
                    "title": "Add watcher regression coverage",
                    "goal": "Add a regression test for watcher behavior.",
                    "constraints": "- use existing test style",
                    "note": "- note: found in repo scan",
                },
            ],
            [f"- inspected repo: {repo_key} @ {base_branch or 'main'}"],
        ),
    )

    created_ids = handle_improve_task(client, service, "IMPROVE-1")

    assert created_ids == ["FOLLOWUP-1", "FOLLOWUP-2"]
    assert client.transitions[-1] == ("IMPROVE-1", "Review")
    assert any("[Improve] Improvement pass" in comment for _, comment in client.issue_comments)


def test_handle_improve_task_preserves_source_repo_and_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient(
        [
            {
                "id": "IMPROVE-REPO",
                "name": "Inspect shorts repo",
                "description": "## Execution\nrepo: ControlPlane\nbase_branch: main\nmode: goal\n\n## Goal\nInspect repo",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: improve"}],
            },
        ]
    )
    service = FakeService()
    service.settings.repos["ControlPlane"] = SimpleNamespace(
        default_branch="main",
        clone_url="git@github.com:Velascat/ControlPlane.git",
        allowed_base_branches=["main"],
    )
    observed: dict[str, str | None] = {}

    def fake_discover(_service: FakeService, *, repo_key: str, base_branch: str | None = None) -> tuple[list[dict[str, str]], list[str]]:
        observed["repo_key"] = repo_key
        observed["base_branch"] = base_branch
        return [], [f"- inspected repo: {repo_key} @ {base_branch}"]

    monkeypatch.setattr("control_plane.entrypoints.worker.main.discover_improvement_candidates", fake_discover)

    created_ids = handle_improve_task(client, service, "IMPROVE-REPO")

    assert created_ids == []
    assert observed == {"repo_key": "ControlPlane", "base_branch": "main"}
    assert client.transitions[-1] == ("IMPROVE-REPO", "Done")
    assert any("ControlPlane @ main" in comment for _, comment in client.issue_comments)


def test_handle_improve_task_reads_execution_target_from_description_html(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient(
        [
            {
                "id": "IMPROVE-HTML",
                "name": "Inspect shorts repo",
                "description": None,
                "description_stripped": None,
                "description_html": (
                    "<div><p>## Execution<br>repo: ControlPlane<br>base_branch: main<br>mode: goal</p>"
                    "<p>## Goal<br>Inspect repo</p></div>"
                ),
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: improve"}],
            },
        ]
    )
    service = FakeService()
    service.settings.repos["ControlPlane"] = SimpleNamespace(
        default_branch="main",
        clone_url="git@github.com:Velascat/ControlPlane.git",
        allowed_base_branches=["main"],
    )
    observed: dict[str, str | None] = {}

    def fake_discover(_service: FakeService, *, repo_key: str, base_branch: str | None = None) -> tuple[list[dict[str, str]], list[str]]:
        observed["repo_key"] = repo_key
        observed["base_branch"] = base_branch
        return [], [f"- inspected repo: {repo_key} @ {base_branch}"]

    monkeypatch.setattr("control_plane.entrypoints.worker.main.discover_improvement_candidates", fake_discover)

    created_ids = handle_improve_task(client, service, "IMPROVE-HTML")

    assert created_ids == []
    assert observed == {"repo_key": "ControlPlane", "base_branch": "main"}
    assert client.transitions[-1] == ("IMPROVE-HTML", "Done")


def test_run_watch_loop_returns_claimed_improve_task_to_ready_after_worker_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient(
        [
            {
                "id": "IMPROVE-ERR",
                "name": "Broken improve",
                "description": "## Execution\nrepo: control-plane\nbase_branch: main\nmode: improve\n\n## Goal\nInspect repo",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: improve"}],
            },
        ]
    )
    service = FakeService()

    def boom(_client: FakePlaneClient, _service: FakeService, _task_id: str) -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr("control_plane.entrypoints.worker.main.handle_improve_task", boom)

    run_watch_loop(
        client,
        service,
        role="improve",
        ready_state="Ready for AI",
        poll_interval_seconds=0,
        max_cycles=1,
    )

    assert ("IMPROVE-ERR", "Running") in client.transitions
    assert client.transitions[-1] == ("IMPROVE-ERR", "Ready for AI")
    assert any("returned to queue after worker error" in comment.lower() for _, comment in client.issue_comments)


def test_handle_test_task_does_not_mark_done_for_internal_only_no_op() -> None:
    client = FakePlaneClient(
        [
            {
                "id": "TEST-NOOP",
                "name": "Verify watcher",
                "description": "## Execution\nrepo: ControlPlane\nbase_branch: main\nmode: test\n\n## Evidence\n- recent commits: abc123 Fix prompt wiring\n- recently changed files: test/conftest.py, src/main.py",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: test"}],
            },
        ]
    )
    service = FakeService(success=True)
    service.run_task = lambda _client, task_id: ExecutionResult(  # type: ignore[assignment]
        run_id="run-noop-test",
        success=True,
        outcome_status="no_op",
        outcome_reason="internal_only_change",
        changed_files=[],
        internal_changed_files=["kodo/config.json"],
        validation_passed=True,
        validation_results=[],
        branch_pushed=False,
        draft_branch_pushed=False,
        push_reason=None,
        pull_request_url=None,
        execution_stderr_excerpt=None,
        summary="run_id=run-noop-test execution=passed validation=passed policy=passed branch_push=not_pushed changed_files=0 internal_changed_files=1 no_op=true",
        artifacts=[],
        policy_violations=[],
        final_status="Blocked",
    )

    created_ids = handle_test_task(client, service, "TEST-NOOP")

    assert created_ids == []
    assert client.created == []
    assert ("TEST-NOOP", "Done") not in client.transitions
    assert any("Verification produced no meaningful repo change" in comment for _, comment in client.issue_comments)
    assert any("selected_evidence: recent commits: abc123 Fix prompt wiring" in comment for _, comment in client.issue_comments)
    assert any("target_area_hint: test/conftest.py, src/main.py" in comment for _, comment in client.issue_comments)


def test_reconcile_stale_running_issues_requeues_never_executed_task() -> None:
    """Task with zero execution attempts (just claimed) → re-queued to Ready for AI."""
    client = FakePlaneClient(
        [
            {
                "id": "GOAL-FRESH",
                "name": "Just claimed goal",
                "state": {"name": "Running"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ]
    )
    store = FakeUsageStore(task_attempts={})

    reconciled = reconcile_stale_running_issues(client, role="goal", ready_state="Ready for AI", usage_store=store)

    assert reconciled == ["GOAL-FRESH"]
    assert ("GOAL-FRESH", "Ready for AI") in client.transitions
    assert ("GOAL-FRESH", "Blocked") not in client.transitions
    assert any("re-queued" in c.lower() or "interrupted" in c.lower() for _, c in client.issue_comments)


def test_reconcile_stale_running_issues_requeues_one_attempt_no_signature() -> None:
    """Task with 1 attempt but no signature → interrupted mid-run, safe to re-queue."""
    client = FakePlaneClient(
        [
            {
                "id": "GOAL-RUN",
                "name": "Stale goal",
                "state": {"name": "Running"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ]
    )
    store = FakeUsageStore(task_attempts={"GOAL-RUN": 1})  # ran once, no signature

    reconciled = reconcile_stale_running_issues(client, role="goal", ready_state="Ready for AI", usage_store=store)

    assert reconciled == ["GOAL-RUN"]
    assert ("GOAL-RUN", "Ready for AI") in client.transitions
    assert ("GOAL-RUN", "Blocked") not in client.transitions


def test_reconcile_stale_running_issues_blocks_when_has_signature() -> None:
    """Task with 1 attempt AND a recorded signature → ran to completion, block for review."""
    client = FakePlaneClient(
        [
            {
                "id": "GOAL-RUN",
                "name": "Stale goal",
                "state": {"name": "Running"},
                "labels": [{"name": "task-kind: goal"}],
            },
            {
                "id": "TEST-RUN",
                "name": "Other role",
                "state": {"name": "Running"},
                "labels": [{"name": "task-kind: test"}],
            },
        ]
    )
    store = FakeUsageStore(task_attempts={"GOAL-RUN": 1}, task_signatures={"GOAL-RUN": "sha:abc123"})

    reconciled = reconcile_stale_running_issues(client, role="goal", ready_state="Ready for AI", usage_store=store)

    assert reconciled == ["GOAL-RUN"]
    assert ("GOAL-RUN", "Blocked") in client.transitions
    assert ("GOAL-RUN", "Ready for AI") not in client.transitions
    assert ("TEST-RUN", "Blocked") not in client.transitions
    assert any("human review" in c.lower() for _, c in client.issue_comments)


def test_reconcile_stale_running_issues_blocks_on_multiple_attempts() -> None:
    """Task with 2+ attempts → retried and failed repeatedly, block for human review."""
    client = FakePlaneClient(
        [
            {
                "id": "GOAL-RETRY",
                "name": "Repeatedly failing goal",
                "state": {"name": "Running"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ]
    )
    store = FakeUsageStore(task_attempts={"GOAL-RETRY": 2})

    reconciled = reconcile_stale_running_issues(client, role="goal", ready_state="Ready for AI", usage_store=store)

    assert reconciled == ["GOAL-RETRY"]
    assert ("GOAL-RETRY", "Blocked") in client.transitions
    assert ("GOAL-RETRY", "Ready for AI") not in client.transitions


def test_handle_blocked_triage_does_not_recurse_for_improve_generated_task() -> None:
    client = FakePlaneClient(
        [
            {
                "id": "BLOCKED-2",
                "name": "Unblock watcher task",
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}, {"name": "source: improve-worker"}],
            },
        ],
        comments={
            "BLOCKED-2": [
                {"comment_html": "<p>[Goal] Execution result</p><ul><li>validation_passed: False</li></ul>"},
            ]
        },
    )
    service = FakeService()

    classification, created_ids = handle_blocked_triage(client, service, "BLOCKED-2")

    assert classification == "validation_failure"
    assert created_ids == []
    assert client.created == []
    assert any("will not create another recursive unblock" in comment for _, comment in client.issue_comments)


def test_handle_blocked_triage_marks_human_attention_for_infra_tooling() -> None:
    client = FakePlaneClient(
        [
            {
                "id": "BLOCKED-3",
                "name": "Auth failure",
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ],
        comments={
            "BLOCKED-3": [
                {"comment_html": "<p>[Goal] Execution result</p><ul><li>execution_stderr: Error: ANTHROPIC_API_KEY not set</li></ul>"},
            ]
        },
    )
    service = FakeService()

    classification, created_ids = handle_blocked_triage(client, service, "BLOCKED-3")

    assert classification == "infra_tooling"
    assert created_ids == []
    assert client.created == []
    assert any("human_attention_required: true" in comment for _, comment in client.issue_comments)


def test_handle_blocked_triage_avoids_duplicate_follow_up_for_same_source_reason() -> None:
    existing_description = """## Execution
repo: control-plane
base_branch: main
mode: goal

## Goal
Existing follow-up

## Context
- original_task_id: BLOCKED-4
- original_task_title: Broken task
- source_worker_role: improve
- source_task_kind: goal
- follow_up_task_kind: goal
- handoff_reason: improve_triage_validation_failure
"""
    client = FakePlaneClient(
        [
            {
                "id": "BLOCKED-4",
                "name": "Broken task",
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}],
            },
            {
                "id": "FOLLOWUP-EXISTING",
                "name": "Resolve blocked Broken task",
                "description": existing_description,
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: goal"}, {"name": "source: improve-worker"}],
            },
        ],
        comments={
            "BLOCKED-4": [
                {"comment_html": "<p>[Goal] Execution result</p><ul><li>validation_passed: False</li></ul>"},
            ]
        },
    )
    service = FakeService()

    classification, created_ids = handle_blocked_triage(client, service, "BLOCKED-4")

    assert classification == "validation_failure"
    assert created_ids == []
    assert len(client.created) == 0


def test_handle_blocked_triage_preserves_source_repo_and_branch_for_follow_up() -> None:
    blocked_description = """## Execution
repo: ControlPlane
base_branch: main
mode: goal

## Goal
Fix my shit
"""
    client = FakePlaneClient(
        [
            {
                "id": "BLOCKED-5",
                "name": "Broken shorts task",
                "description": blocked_description,
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ],
        comments={
            "BLOCKED-5": [
                {"comment_html": "<p>[Goal] Execution result</p><ul><li>validation_passed: False</li></ul>"},
            ]
        },
    )
    service = FakeService()
    service.settings.repos["ControlPlane"] = SimpleNamespace(
        default_branch="main",
        clone_url="git@github.com:Velascat/ControlPlane.git",
    )

    classification, created_ids = handle_blocked_triage(client, service, "BLOCKED-5")

    assert classification == "validation_failure"
    assert created_ids == ["FOLLOWUP-1"]
    assert "repo: ControlPlane" in str(client.created[0]["description"])
    assert "base_branch: main" in str(client.created[0]["description"])


def test_build_improve_triage_result_detects_repeated_pattern() -> None:
    """Follow-up goal is created when the same classification appears >= 3 times."""
    client = FakePlaneClient(
        [
            {
                "id": "BLOCKED-A",
                "name": "Broken task A",
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}],
            },
            {
                "id": "BLOCKED-B",
                "name": "Broken task B",
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}],
            },
            {
                "id": "BLOCKED-C",
                "name": "Broken task C",
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}],
            },
            {
                "id": "BLOCKED-D",
                "name": "Broken task D",
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}],
            },
        ],
        comments={
            "BLOCKED-A": [
                {"comment_html": "<p>[Improve] Blocked triage</p><ul><li>blocked_classification: validation_failure</li></ul>"},
            ],
            "BLOCKED-B": [
                {"comment_html": "<p>[Improve] Blocked triage</p><ul><li>blocked_classification: validation_failure</li></ul>"},
            ],
            "BLOCKED-C": [
                {"comment_html": "<p>[Improve] Blocked triage</p><ul><li>blocked_classification: validation_failure</li></ul>"},
            ],
            "BLOCKED-D": [
                {"comment_html": "<p>[Goal] Execution result</p><ul><li>validation_passed: False</li></ul>"},
            ],
        },
    )

    triage = build_improve_triage_result(client, client.fetch_issue("BLOCKED-D"), client.list_comments("BLOCKED-D"))

    assert triage.classification == "validation_failure"
    assert triage.follow_up is not None
    assert triage.follow_up.handoff_reason == "improve_pattern_validation_failure"
    assert "repeated" in triage.reason_summary.lower()


def test_build_improve_triage_result_no_repeated_pattern_below_threshold() -> None:
    """Counts of 1 and 2 should NOT trigger the repeated-pattern escalation path."""
    for num_prior in (1, 2):
        blocked_issues = [
            {
                "id": f"BLOCKED-{i}",
                "name": f"Broken task {i}",
                "state": {"name": "Blocked"},
                "labels": [{"name": "task-kind: goal"}],
            }
            for i in range(num_prior + 1)  # num_prior classified + 1 current
        ]
        comments: dict[str, list[dict[str, str]]] = {}
        for i in range(num_prior):
            comments[f"BLOCKED-{i}"] = [
                {"comment_html": "<p>[Improve] Blocked triage</p><ul><li>blocked_classification: validation_failure</li></ul>"},
            ]
        current_id = f"BLOCKED-{num_prior}"
        comments[current_id] = [
            {"comment_html": "<p>[Goal] Execution result</p><ul><li>validation_passed: False</li></ul>"},
        ]
        client = FakePlaneClient(blocked_issues, comments=comments)

        triage = build_improve_triage_result(client, client.fetch_issue(current_id), client.list_comments(current_id))

        # The individual triage follow-up may still be created, but the
        # repeated-pattern escalation (improve_pattern_*) must NOT fire.
        if triage.follow_up is not None:
            assert not triage.follow_up.handoff_reason.startswith("improve_pattern_"), (
                f"Expected no repeated-pattern escalation with {num_prior} prior occurrences"
            )
        assert "repeated" not in (triage.reason_summary or "").lower(), (
            f"Expected no 'repeated' language in reason_summary with {num_prior} prior occurrences"
        )


def test_run_watch_loop_uses_backoff_on_rate_limit(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    request = httpx.Request("GET", "http://plane.local/work-items/")
    response = httpx.Response(429, request=request)
    attempts = {"count": 0}

    class RateLimitedClient(FakePlaneClient):
        def list_issues(self) -> list[dict[str, object]]:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.HTTPStatusError("rate limited", request=request, response=response)
            return []

    client = RateLimitedClient([])
    service = FakeService()
    sleeps: list[int] = []

    monkeypatch.setattr("control_plane.entrypoints.worker.main.time.sleep", lambda seconds: sleeps.append(seconds))

    with caplog.at_level(logging.INFO):
        run_watch_loop(
            client,
            service,
            role="goal",
            ready_state="Ready for AI",
            poll_interval_seconds=3,
            max_cycles=2,
        )

    assert sleeps == [12]
    assert any("watch_rate_limited" in record.message for record in caplog.records)


def test_handle_propose_cycle_creates_bounded_goal_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient([])
    service = FakeService()
    now = datetime(2026, 3, 31, 8, 0, tzinfo=UTC)

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.build_proposal_candidates",
        lambda _client, _service, **_kwargs: (
            [
                ProposalSpec(
                    task_kind="goal",
                    title="Address repeated validation failures",
                    goal_text="Fix the repeated validation failure pattern.",
                    reason_summary="Repeated blocked-task validation failures were observed.",
                    source_signal="blocked_pattern:validation_failure",
                    confidence="high",
                    recommended_state="Ready for AI",
                    handoff_reason="propose_pattern_validation_failure",
                    dedup_key="blocked_pattern:validation_failure",
                    constraints_text="- keep the task bounded",
                )
            ],
            ["board_idle: true"],
            True,
        ),
    )

    result = handle_propose_cycle(client, service, status_dir=tmp_path, now=now)

    assert result.decision == "tasks_created"
    assert result.created_task_ids == ["FOLLOWUP-1"]
    assert client.created[0]["state"] == {"name": "Ready for AI"}


def test_handle_propose_cycle_passes_single_enabled_repo_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = FakePlaneClient([])
    service = FakeService()
    service.settings.repos["ControlPlane"] = SimpleNamespace(
        default_branch="main",
        clone_url="git@github.com:Velascat/ControlPlane.git",
        allowed_base_branches=["main"],
    )
    monkeypatch.setattr("control_plane.entrypoints.worker.main.proposal_repo_keys", lambda _service: ["ControlPlane"])

    def fake_build(
        _client: FakePlaneClient,
        _service: FakeService,
        *,
        repo_key: str | None = None,
        issues: list[dict[str, object]] | None = None,
    ) -> tuple[list[ProposalSpec], list[str], bool]:
        assert repo_key == "ControlPlane"
        assert issues == []
        return (
            [
                ProposalSpec(
                    repo_key="ControlPlane",
                    task_kind="goal",
                    title="Implement next bounded repo improvement",
                    goal_text="Inspect the repo.",
                    reason_summary="Idle board.",
                    source_signal="ControlPlane:idle_board",
                    confidence="medium",
                    recommended_state="Ready for AI",
                    handoff_reason="propose_idle_board_scan",
                    dedup_key="ControlPlane:idle_board:repo_scan",
                    evidence_lines=["recent commits: abc123 Fix prompt wiring"],
                )
            ],
            ["repo: ControlPlane"],
            True,
        )

    monkeypatch.setattr("control_plane.entrypoints.worker.main.build_proposal_candidates", fake_build)

    result = handle_propose_cycle(client, service, status_dir=tmp_path, now=datetime(2026, 3, 31, 8, 0, tzinfo=UTC))

    assert result.decision == "tasks_created"
    assert result.created_task_ids == ["FOLLOWUP-1"]
    assert "repo: ControlPlane" in str(client.created[0]["description"])
    assert "base_branch: main" in str(client.created[0]["description"])
    assert client.created[0]["labels"] == [
        {"name": "task-kind: goal"},
        {"name": "source: proposer"},
        {"name": "reason: controlplane_idle_board"},
    ]
    assert "## Evidence" in str(client.created[0]["description"])
    assert any("[Propose] Autonomous task created" in comment for _, comment in client.issue_comments)


def test_handle_propose_cycle_respects_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient([])
    service = FakeService()
    first = datetime(2026, 3, 31, 8, 0, tzinfo=UTC)
    second = first + timedelta(minutes=5)

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.build_proposal_candidates",
        lambda _client, _service, **_kwargs: (
            [
                ProposalSpec(
                    task_kind="goal",
                    title="Implement next bounded repo improvement",
                    goal_text="Inspect repo state and propose bounded tasks.",
                    reason_summary="Idle board fallback.",
                    source_signal="idle_board",
                    confidence="medium",
                    recommended_state="Ready for AI",
                    handoff_reason="propose_idle_board_scan",
                    dedup_key="idle_board:repo_scan",
                )
            ],
            ["board_idle: true"],
            True,
        ),
    )

    first_result = handle_propose_cycle(client, service, status_dir=tmp_path, now=first)
    second_result = handle_propose_cycle(client, service, status_dir=tmp_path, now=second)

    assert first_result.decision == "tasks_created"
    assert second_result.decision == "cooldown_active"
    assert len(client.created) == 1


def test_handle_propose_cycle_dedups_existing_open_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    existing_description = """## Execution
repo: control-plane
base_branch: main
mode: goal

## Goal
Existing proposal

## Context
- source_worker_role: propose
- follow_up_task_kind: goal
- handoff_reason: propose_pattern_validation_failure
- source_signal: blocked_pattern:validation_failure
- confidence: high
- proposal_dedup_key: blocked_pattern:validation_failure
"""
    client = FakePlaneClient(
        [
            {
                "id": "EXISTING-1",
                "name": "Address repeated validation failures",
                "description": existing_description,
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: goal"}, {"name": "source: proposer"}],
            }
        ]
    )
    service = FakeService()
    now = datetime(2026, 3, 31, 8, 0, tzinfo=UTC)

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.build_proposal_candidates",
        lambda _client, _service, **_kwargs: (
            [
                ProposalSpec(
                    task_kind="goal",
                    title="Address repeated validation failures",
                    goal_text="Fix the repeated validation failure pattern.",
                    reason_summary="Repeated blocked-task validation failures were observed.",
                    source_signal="blocked_pattern:validation_failure",
                    confidence="high",
                    recommended_state="Ready for AI",
                    handoff_reason="propose_pattern_validation_failure",
                    dedup_key="blocked_pattern:validation_failure",
                )
            ],
            ["board_idle: true"],
            True,
        ),
    )

    result = handle_propose_cycle(client, service, status_dir=tmp_path, now=now)

    assert result.decision == "deduped"
    assert result.created_task_ids == []
    assert client.created == []


def test_run_watch_loop_propose_role_creates_task_when_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient([])
    service = FakeService()

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.build_proposal_candidates",
        lambda _client, _service, **_kwargs: (
            [
                ProposalSpec(
                    task_kind="goal",
                    title="Implement next bounded repo improvement",
                    goal_text="Inspect repo state and propose bounded tasks.",
                    reason_summary="Idle board fallback.",
                    source_signal="idle_board",
                    confidence="medium",
                    recommended_state="Ready for AI",
                    handoff_reason="propose_idle_board_scan",
                    dedup_key="idle_board:repo_scan",
                )
            ],
            ["board_idle: true"],
            True,
        ),
    )

    run_watch_loop(
        client,
        service,
        role="propose",
        ready_state="Ready for AI",
        poll_interval_seconds=0,
        max_cycles=1,
        status_dir=tmp_path,
    )

    assert client.created
    assert client.created[0]["labels"] == [
        {"name": "task-kind: goal"},
        {"name": "source: proposer"},
        {"name": "reason: idle_board"},
    ]


def test_build_proposal_candidates_idle_board_fallback_includes_recent_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient([])
    service = FakeService()
    service.settings.repos["ControlPlane"] = SimpleNamespace(
        default_branch="main",
        clone_url="git@github.com:Velascat/ControlPlane.git",
        allowed_base_branches=["main"],
    )

    def fake_discover(
        _service: FakeService,
        *,
        repo_key: str,
        base_branch: str | None = None,
    ) -> tuple[list[dict[str, str]], list[str]]:
        assert repo_key == "ControlPlane"
        assert base_branch == "main"
        return (
            [],
            [
                "- inspected repo: ControlPlane @ main",
                "- recent commits: abc123 Fix prompt wiring | def456 Add coverage",
                "- recently changed files: test/conftest.py, src/main.py",
                "- report signal: repeated pytest collection errors",
            ],
        )

    monkeypatch.setattr("control_plane.entrypoints.worker.main.discover_improvement_candidates", fake_discover)
    from control_plane.entrypoints.worker.main import build_proposal_candidates

    proposals, notes, board_idle = build_proposal_candidates(client, service, repo_key="ControlPlane", issues=[])

    assert board_idle is True
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.title == "Implement bounded improvement in test/conftest.py"
    assert "Start with `test/conftest.py` as the target area" in proposal.goal_text
    assert "prefer `test/conftest.py`" in str(proposal.constraints_text)
    assert proposal.evidence_lines == [
        "report signal: repeated pytest collection errors",
        "recently changed files: test/conftest.py, src/main.py",
        "recent commits: abc123 Fix prompt wiring | def456 Add coverage",
        "inspected repo: ControlPlane @ main",
    ]
    assert any("fallback_proposal: idle_board evidence-anchored repo improvement" == note for note in notes)


def test_evidence_lines_from_notes_prioritizes_actionable_signals() -> None:
    from control_plane.entrypoints.worker.main import evidence_lines_from_notes

    evidence_lines = evidence_lines_from_notes(
        [
            "- inspected repo: ControlPlane @ main",
            "- top-level entries: src, docs",
            "- recently changed files: test/conftest.py, src/main.py",
            "- recent commits: abc123 Fix prompt wiring | def456 Add coverage",
            "- report signal: repeated pytest collection errors",
        ]
    )

    assert evidence_lines == [
        "report signal: repeated pytest collection errors",
        "recently changed files: test/conftest.py, src/main.py",
        "recent commits: abc123 Fix prompt wiring | def456 Add coverage",
        "inspected repo: ControlPlane @ main",
        "top-level entries: src, docs",
    ]


def test_build_proposal_candidates_idle_board_fallback_suppresses_weak_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient([])
    service = FakeService()

    def fake_discover(
        _service: FakeService,
        *,
        repo_key: str,
        base_branch: str | None = None,
    ) -> tuple[list[dict[str, str]], list[str]]:
        return (
            [],
            [
                "- inspected repo: control-plane @ main",
                "- top-level entries: src, tests, docs",
                "- tests directory present: yes",
                "- docs directory present: yes",
            ],
        )

    monkeypatch.setattr("control_plane.entrypoints.worker.main.discover_improvement_candidates", fake_discover)
    from control_plane.entrypoints.worker.main import build_proposal_candidates

    proposals, notes, board_idle = build_proposal_candidates(client, service, repo_key="control-plane", issues=[])

    assert board_idle is True
    assert proposals == []
    assert "fallback_suppressed: idle_board evidence too weak" in notes


def test_handle_propose_cycle_suppresses_when_budget_too_low(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("CONTROL_PLANE_MAX_EXEC_PER_HOUR", "1")
    monkeypatch.setenv("CONTROL_PLANE_MIN_REMAINING_EXEC_FOR_PROPOSALS", "1")
    client = FakePlaneClient([])
    service = FakeService()
    service.usage_store.record_execution(  # type: ignore[attr-defined]
        role="goal",
        task_id="TASK-1",
        signature="sig-1",
        now=datetime(2026, 3, 31, 8, 0, tzinfo=UTC),
    )

    result = handle_propose_cycle(
        client,
        service,
        status_dir=tmp_path,
        now=datetime(2026, 3, 31, 8, 10, tzinfo=UTC),
    )

    assert result.created_task_ids == []
    assert result.decision == "proposal_budget_too_low"


# ---------------------------------------------------------------------------
# _has_active_pr_review
# ---------------------------------------------------------------------------


def test_has_active_pr_review_true_when_file_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_has_active_pr_review returns True when the state file exists."""
    from control_plane.entrypoints.worker.main import _has_active_pr_review

    monkeypatch.setattr("control_plane.entrypoints.worker.main._PR_REVIEW_STATE_DIR", tmp_path)
    (tmp_path / "task-123.json").write_text("{}")
    assert _has_active_pr_review("task-123") is True


def test_has_active_pr_review_false_when_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_has_active_pr_review returns False when no state file exists."""
    from control_plane.entrypoints.worker.main import _has_active_pr_review

    monkeypatch.setattr("control_plane.entrypoints.worker.main._PR_REVIEW_STATE_DIR", tmp_path)
    assert _has_active_pr_review("task-456") is False


# ── proposed_index (recently_proposed / record_proposed) ─────────────────────


def test_recently_proposed_false_when_index_empty() -> None:
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    memory: dict = {"proposed_index": {}}
    assert recently_proposed(memory, title="Add tests for client.py", dedup_key="k|test|client", now=now) is False


def test_recently_proposed_true_by_title_within_window() -> None:
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    yesterday = (now.timestamp() - 86400)
    memory: dict = {"proposed_index": {"add tests for client.py": yesterday}}
    assert recently_proposed(memory, title="Add tests for client.py", dedup_key="k|test|client", now=now) is True


def test_recently_proposed_true_by_dedup_key_within_window() -> None:
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    yesterday = now.timestamp() - 86400
    memory: dict = {"proposed_index": {"k|test|client": yesterday}}
    assert recently_proposed(memory, title="Add tests for client.py", dedup_key="k|test|client", now=now) is True


def test_recently_proposed_false_when_entry_outside_window() -> None:
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    eight_days_ago = now.timestamp() - (8 * 86400)
    memory: dict = {"proposed_index": {"add tests for client.py": eight_days_ago}}
    assert recently_proposed(memory, title="Add tests for client.py", dedup_key="k|test|client", now=now) is False


def test_recently_proposed_prunes_stale_entries() -> None:
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    fresh = now.timestamp() - 3600
    stale = now.timestamp() - (8 * 86400)
    memory: dict = {"proposed_index": {"fresh_title": fresh, "old_title": stale}}
    recently_proposed(memory, title="irrelevant", dedup_key="irrelevant_key", now=now)
    assert "fresh_title" in memory["proposed_index"]
    assert "old_title" not in memory["proposed_index"]


def test_record_proposed_writes_both_keys() -> None:
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    memory: dict = {"proposed_index": {}}
    record_proposed(memory, title="Fix lint issues", dedup_key="k|lint_fix|src", now=now)
    assert "fix lint issues" in memory["proposed_index"]
    assert "k|lint_fix|src" in memory["proposed_index"]


def test_handle_propose_cycle_skips_recently_proposed_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks in the proposed_index within the window are not re-created."""
    client = FakePlaneClient([])
    service = FakeService()
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.build_proposal_candidates",
        lambda _client, _service, **_kwargs: (
            [
                ProposalSpec(
                    task_kind="goal",
                    title="Add tests for client.py",
                    goal_text="Write tests.",
                    reason_summary="Missing coverage.",
                    source_signal="repo_scan_test",
                    confidence="high",
                    recommended_state="Ready for AI",
                    handoff_reason="propose_idle_board_scan",
                    dedup_key="k|test_coverage|client_py",
                )
            ],
            [],
            True,
        ),
    )

    # Pre-populate the index with this title from 1 day ago (within 7-day window)
    memory_path = tmp_path / "propose.memory.json"
    import json as _json
    memory_path.write_text(_json.dumps({
        "last_proposal_at": None,
        "proposal_timestamps": [],
        "proposed_index": {"add tests for client.py": now.timestamp() - 86400},
    }))

    result = handle_propose_cycle(client, service, status_dir=tmp_path, now=now)

    assert result.created_task_ids == []
    assert client.created == []


def test_handle_propose_cycle_records_in_index_after_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a task is created, its title and dedup_key appear in proposed_index."""
    client = FakePlaneClient([])
    service = FakeService()
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.build_proposal_candidates",
        lambda _client, _service, **_kwargs: (
            [
                ProposalSpec(
                    task_kind="goal",
                    title="Fix lint in src/",
                    goal_text="Fix lint.",
                    reason_summary="Lint violations detected.",
                    source_signal="repo_scan_lint",
                    confidence="high",
                    recommended_state="Backlog",
                    handoff_reason="propose_idle_board_scan",
                    dedup_key="k|lint_fix|src",
                )
            ],
            [],
            True,
        ),
    )

    handle_propose_cycle(client, service, status_dir=tmp_path, now=now)

    import json as _json
    saved = _json.loads((tmp_path / "propose.memory.json").read_text())
    assert "fix lint in src/" in saved["proposed_index"]
    assert "k|lint_fix|src" in saved["proposed_index"]


# ---------------------------------------------------------------------------
# extract_triage_follow_up_ids / blocked_resolution_is_complete
# ---------------------------------------------------------------------------

def _triage_comment(follow_up_ids: str) -> dict:
    """Build a minimal fake triage comment with the given follow_up_task_ids string."""
    body = (
        f"[Improve] Blocked triage\n"
        f"- task_id: SOME-ID\n"
        f"- follow_up_task_ids: {follow_up_ids}\n"
    )
    return {"comment_html": f"<p>{body}</p>"}


def test_extract_triage_follow_up_ids_returns_uuids() -> None:
    client = FakePlaneClient(
        [{"id": "TASK-1", "state": {"name": "Blocked"}}],
        comments={"TASK-1": [_triage_comment("uuid-a, uuid-b")]},
    )
    assert extract_triage_follow_up_ids(client, "TASK-1") == ["uuid-a", "uuid-b"]


def test_extract_triage_follow_up_ids_none_returns_empty() -> None:
    client = FakePlaneClient(
        [{"id": "TASK-1", "state": {"name": "Blocked"}}],
        comments={"TASK-1": [_triage_comment("none")]},
    )
    assert extract_triage_follow_up_ids(client, "TASK-1") == []


def test_extract_triage_follow_up_ids_no_triage_comment_returns_empty() -> None:
    client = FakePlaneClient(
        [{"id": "TASK-1", "state": {"name": "Blocked"}}],
        comments={"TASK-1": [{"comment_html": "<p>some other comment</p>"}]},
    )
    assert extract_triage_follow_up_ids(client, "TASK-1") == []


def test_blocked_resolution_is_complete_all_done() -> None:
    client = FakePlaneClient(
        [
            {"id": "TASK-1", "state": {"name": "Blocked"}},
            {"id": "RESOLVE-1", "state": {"name": "Done"}},
        ],
        comments={"TASK-1": [_triage_comment("RESOLVE-1")]},
    )
    assert blocked_resolution_is_complete(client, "TASK-1") is True


def test_blocked_resolution_is_complete_still_running() -> None:
    client = FakePlaneClient(
        [
            {"id": "TASK-1", "state": {"name": "Blocked"}},
            {"id": "RESOLVE-1", "state": {"name": "Running"}},
        ],
        comments={"TASK-1": [_triage_comment("RESOLVE-1")]},
    )
    assert blocked_resolution_is_complete(client, "TASK-1") is False


def test_blocked_resolution_is_complete_no_follow_ups_returns_false() -> None:
    client = FakePlaneClient(
        [{"id": "TASK-1", "state": {"name": "Blocked"}}],
        comments={"TASK-1": [_triage_comment("none")]},
    )
    assert blocked_resolution_is_complete(client, "TASK-1") is False


def test_blocked_resolution_is_complete_already_unblocked_returns_false() -> None:
    """If the unblock marker is already on the task, do not re-unblock it."""
    unblock_comment = {"comment_html": f"<p>{UNBLOCK_COMMENT_MARKER} — task unblocked</p>"}
    client = FakePlaneClient(
        [
            {"id": "TASK-1", "state": {"name": "Blocked"}},
            {"id": "RESOLVE-1", "state": {"name": "Done"}},
        ],
        comments={"TASK-1": [_triage_comment("RESOLVE-1"), unblock_comment]},
    )
    assert blocked_resolution_is_complete(client, "TASK-1") is False


def test_run_watch_loop_improve_auto_unblocks_when_resolution_done(monkeypatch) -> None:
    """improve watcher should move a blocked task to Ready for AI once its resolution task is Done."""
    blocked_task = {
        "id": "BLOCKED-1",
        "name": "Decompose foo.py",
        "state": {"name": "Blocked"},
        "description": "## Execution\nrepo: control-plane\nmode: goal\n",
        "labels": [{"name": "task-kind: goal"}],
    }
    resolve_task = {
        "id": "RESOLVE-1",
        "name": "Resolve blocked Decompose foo.py",
        "state": {"name": "Done"},
        "description": "## Execution\nrepo: control-plane\nmode: goal\n",
        "labels": [{"name": "task-kind: goal"}],
    }
    triage_comment = _triage_comment("RESOLVE-1")
    # Also add the triage marker text so blocked_issue_already_triaged returns True
    triage_comment["comment_html"] = "<p>[Improve] Blocked triage\n- follow_up_task_ids: RESOLVE-1\n</p>"
    client = FakePlaneClient(
        [blocked_task, resolve_task],
        comments={"BLOCKED-1": [triage_comment]},
    )
    service = FakeService()
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.reconcile_stale_running_issues",
        lambda *a, **kw: None,
    )

    run_watch_loop(
        client,
        service,
        role="improve",
        ready_state="Ready for AI",
        poll_interval_seconds=3,
        max_cycles=1,
    )

    # Task should have been claimed (Running) then moved to Ready for AI
    assert ("BLOCKED-1", "Running") in client.transitions
    assert ("BLOCKED-1", "Ready for AI") in client.transitions
    # A comment about resolution should have been added
    assert any(UNBLOCK_COMMENT_MARKER in c for _, c in client.issue_comments)
