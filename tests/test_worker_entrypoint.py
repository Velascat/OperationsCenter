import logging
from types import SimpleNamespace

import httpx
import pytest

from control_plane.domain.models import ExecutionResult
from control_plane.entrypoints.worker.main import (
    classify_blocked_issue,
    handle_goal_task,
    handle_improve_task,
    handle_blocked_triage,
    issue_status_name,
    issue_task_kind,
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
        self.runs: list[str] = []
        self.settings = SimpleNamespace(
            repos={
                "control-plane": SimpleNamespace(
                    default_branch="main",
                )
            }
        )
        self._success = success

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
        [{"comment_html": "<p>AI execution result</p><ul><li>validation_passed: False</li></ul>"}],
    )
    assert classification == "validation failure"
    assert "Validation failed" in rationale


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


def test_run_watch_loop_improve_role_triages_blocked_task() -> None:
    client = FakePlaneClient(
        [
            {"id": "BLOCKED-1", "name": "Broken task", "state": {"name": "Blocked"}, "labels": [{"name": "task-kind: goal"}]},
        ],
        comments={
            "BLOCKED-1": [
                {"comment_html": "<p>AI execution result</p><ul><li>validation_passed: False</li></ul>"},
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
    assert any("Blocked triage result" in comment for _, comment in client.issue_comments)


def test_handle_goal_task_creates_improve_follow_up_for_noop_failure() -> None:
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
        summary="failed without changes",
        artifacts=[],
        policy_violations=[],
    )

    created_ids = handle_goal_task(client, service, "GOAL-1")

    assert created_ids == ["FOLLOWUP-1"]
    assert client.created[0]["labels"] == [
        {"name": "task-kind: improve"},
        {"name": "source: goal-worker"},
    ]
    assert any("Goal worker created an improve follow-up task" in comment for _, comment in client.issue_comments)


def test_handle_improve_task_discovers_repo_follow_ups(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePlaneClient(
        [
            {
                "id": "IMPROVE-1",
                "name": "Inspect repo",
                "state": {"name": "Ready for AI"},
                "labels": [{"name": "task-kind: improve"}],
            },
        ]
    )
    service = FakeService()

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.discover_improvement_candidates",
        lambda _service: (
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
            ["- inspected repo: control-plane @ main"],
        ),
    )

    created_ids = handle_improve_task(client, service, "IMPROVE-1")

    assert created_ids == ["FOLLOWUP-1", "FOLLOWUP-2"]
    assert client.transitions[-1] == ("IMPROVE-1", "Review")
    assert any("Improve worker result" in comment for _, comment in client.issue_comments)


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
                {"comment_html": "<p>AI execution result</p><ul><li>validation_passed: False</li></ul>"},
            ]
        },
    )
    service = FakeService()

    classification, created_ids = handle_blocked_triage(client, service, "BLOCKED-2")

    assert classification == "validation failure"
    assert created_ids == []
    assert client.created == []
    assert any("will not create another recursive unblock" in comment for _, comment in client.issue_comments)


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
