import logging

import pytest

from control_plane.entrypoints.worker.main import issue_status_name, issue_task_kind, run_watch_loop, select_ready_task_id


class FakePlaneClient:
    def __init__(self, issues: list[dict[str, object]]) -> None:
        self._issues = issues

    def list_issues(self) -> list[dict[str, object]]:
        return self._issues

    def fetch_issue(self, task_id: str) -> dict[str, object]:
        for issue in self._issues:
            if issue["id"] == task_id:
                return issue
        raise KeyError(task_id)


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


def test_issue_task_kind_defaults_to_goal_without_label() -> None:
    assert issue_task_kind({"labels": []}) == "goal"


def test_issue_task_kind_extracts_label_value() -> None:
    assert issue_task_kind({"labels": [{"name": "task-kind: test"}]}) == "test"


class FakeWatchPlaneClient(FakePlaneClient):
    def __init__(self, issues: list[dict[str, object]]) -> None:
        super().__init__(issues)
        self.transitions: list[tuple[str, str]] = []

    def transition_issue(self, task_id: str, state: str) -> None:
        self.transitions.append((task_id, state))


class FakeService:
    def __init__(self) -> None:
        self.runs: list[str] = []

    def run_task(self, client: FakeWatchPlaneClient, task_id: str) -> None:  # noqa: ARG002
        self.runs.append(task_id)


def test_run_watch_loop_claims_and_runs_one_goal_task(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeWatchPlaneClient(
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
