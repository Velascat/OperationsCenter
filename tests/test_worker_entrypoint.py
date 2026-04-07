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
    _STALE_AUTONOMY_CANCEL_MARKER,
    _check_task_pr_merged,
    _extract_filename_tokens,
    _get_kodo_version,
    _has_conflict_with_active_task,
    _heartbeat_path,
    _in_maintenance_window,
    _is_multi_step_task,
    _is_self_repo,
    _pr_number_from_url,
    _proposal_matches_focus_areas,
    _record_execution_artifact,
    _scheduled_tasks_due,
    _self_modify_approved,
    _split_oversized_finding,
    blocked_resolution_is_complete,
    board_health_check,
    build_improve_triage_result,
    build_multi_step_plan,
    check_heartbeats,
    classify_blocked_issue,
    classify_execution_result,
    detect_post_merge_regressions,
    execution_gate_decision,
    extract_triage_follow_up_ids,
    handle_feedback_loop_scan,
    handle_goal_task,
    handle_improve_task,
    handle_propose_cycle,
    handle_blocked_triage,
    handle_stale_autonomy_task_scan,
    handle_test_task,
    handle_workspace_health_check,
    issue_status_name,
    issue_task_kind,
    parse_task_dependencies,
    recently_proposed,
    record_proposed,
    reconcile_stale_running_issues,
    run_parallel_watch_loop,
    run_watch_loop,
    select_ready_task_id,
    task_dependencies_met,
    select_watch_candidate,
    validate_credentials,
    validate_task_pre_execution,
    write_heartbeat,
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


# ---------------------------------------------------------------------------
# promote_backlog_tasks
# ---------------------------------------------------------------------------

def test_promote_backlog_tasks_promotes_proposer_tasks() -> None:
    """Backlog tasks with source: proposer label are promoted to Ready for AI."""
    from control_plane.entrypoints.worker.main import promote_backlog_tasks
    client = FakePlaneClient([
        {"id": "B-1", "name": "Decompose foo.py", "state": {"name": "Backlog"}, "labels": [{"name": "task-kind: goal"}, {"name": "source: proposer"}]},
        {"id": "B-2", "name": "Add tests", "state": {"name": "Backlog"}, "labels": [{"name": "task-kind: goal"}, {"name": "source: autonomy"}]},
        {"id": "MANUAL", "name": "Manual task", "state": {"name": "Backlog"}, "labels": [{"name": "task-kind: goal"}]},
    ])
    promoted = promote_backlog_tasks(client, client.list_issues(), max_promotions=2)
    assert set(promoted) == {"B-1", "B-2"}
    assert ("B-1", "Ready for AI") in client.transitions
    assert ("B-2", "Ready for AI") in client.transitions
    assert ("MANUAL", "Ready for AI") not in client.transitions


def test_promote_backlog_tasks_respects_max() -> None:
    from control_plane.entrypoints.worker.main import promote_backlog_tasks
    issues = [
        {"id": f"B-{i}", "name": f"Task {i}", "state": {"name": "Backlog"}, "labels": [{"name": "task-kind: goal"}, {"name": "source: proposer"}]}
        for i in range(5)
    ]
    client = FakePlaneClient(issues)
    promoted = promote_backlog_tasks(client, client.list_issues(), max_promotions=2)
    assert len(promoted) == 2


# ---------------------------------------------------------------------------
# active_task_count_from_issues
# ---------------------------------------------------------------------------

def test_active_task_count_counts_ready_and_running() -> None:
    from control_plane.entrypoints.worker.main import active_task_count_from_issues
    issues = [
        {"id": "1", "state": {"name": "Ready for AI"}, "labels": [{"name": "task-kind: goal"}]},
        {"id": "2", "state": {"name": "Running"}, "labels": [{"name": "task-kind: goal"}]},
        {"id": "3", "state": {"name": "Blocked"}, "labels": [{"name": "task-kind: goal"}]},
        {"id": "4", "state": {"name": "Running"}, "labels": [{"name": "task-kind: improve"}]},  # improve, not counted
    ]
    assert active_task_count_from_issues(issues) == 2


# ---------------------------------------------------------------------------
# _normalise_proposal_title
# ---------------------------------------------------------------------------

def test_normalise_strips_line_count_suffix() -> None:
    from control_plane.entrypoints.worker.main import _normalise_proposal_title
    assert _normalise_proposal_title("Decompose main.py (2823L, 15 oversized function(s))") == \
           _normalise_proposal_title("Decompose main.py (2852L, 16 oversized function(s))")


def test_normalise_strips_leading_count() -> None:
    from control_plane.entrypoints.worker.main import _normalise_proposal_title
    assert _normalise_proposal_title("Fix 213 type error(s) found by ty") == \
           _normalise_proposal_title("Fix 212 type error(s) found by ty")


def test_recently_proposed_matches_despite_metric_drift(monkeypatch) -> None:
    """Scan drift in line counts should not allow re-proposing the same task."""
    from control_plane.entrypoints.worker.main import recently_proposed, record_proposed
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    memory: dict = {"proposed_index": {}, "proposal_timestamps": [], "last_proposal_at": None}
    record_proposed(memory, title="Decompose main.py (2823L, 15 oversized function(s))", dedup_key="k|decompose|main_py", now=now)
    later = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    assert recently_proposed(memory, title="Decompose main.py (2852L, 16 oversized function(s))", dedup_key="k|decompose|main_py_v2", now=later)


# ---------------------------------------------------------------------------
# issue_priority
# ---------------------------------------------------------------------------

def test_issue_priority_high_sorts_first() -> None:
    from control_plane.entrypoints.worker.main import issue_priority
    high = {"labels": [{"name": "priority: high"}]}
    medium = {"labels": [{"name": "priority: medium"}]}
    low = {"labels": [{"name": "priority: low"}]}
    unset = {"labels": []}
    assert issue_priority(high) < issue_priority(medium) < issue_priority(low) < issue_priority(unset)


# ---------------------------------------------------------------------------
# blocked_issue_is_stale
# ---------------------------------------------------------------------------

def test_blocked_issue_is_stale_old_task() -> None:
    from control_plane.entrypoints.worker.main import blocked_issue_is_stale
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    old = {"updated_at": "2026-03-01T00:00:00+00:00"}
    assert blocked_issue_is_stale(old, now=now) is True


def test_blocked_issue_is_stale_recent_task() -> None:
    from control_plane.entrypoints.worker.main import blocked_issue_is_stale
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    recent = {"updated_at": "2026-04-05T00:00:00+00:00"}
    assert blocked_issue_is_stale(recent, now=now) is False


# ---------------------------------------------------------------------------
# Item 1: _record_execution_artifact / UsageStore.get_task_artifact
# ---------------------------------------------------------------------------

def test_record_execution_artifact_persists_fields() -> None:
    service = FakeService(success=True)
    result = ExecutionResult(
        run_id="run-art",
        success=True,
        changed_files=["src/foo.py", "src/bar.py"],
        validation_passed=True,
        validation_results=[],
        branch_pushed=True,
        draft_branch_pushed=False,
        push_reason=None,
        pull_request_url="https://github.com/owner/repo/pull/42",
        summary="ok",
        artifacts=[],
        policy_violations=[],
    )
    _record_execution_artifact(service, "TASK-ART", result)
    artifact = service.usage_store.get_task_artifact("TASK-ART")
    assert artifact is not None
    assert artifact["changed_files"] == ["src/foo.py", "src/bar.py"]
    assert artifact["pull_request_url"] == "https://github.com/owner/repo/pull/42"
    assert artifact["success"] is True
    assert "recorded_at" in artifact


def test_record_execution_artifact_missing_task_returns_none() -> None:
    service = FakeService()
    assert service.usage_store.get_task_artifact("NO-SUCH") is None


def test_rewrite_worker_summary_records_artifact(monkeypatch) -> None:
    """rewrite_worker_summary with task_id writes artifact as a side-effect."""
    from control_plane.entrypoints.worker.main import rewrite_worker_summary
    service = FakeService(success=False)
    result = ExecutionResult(
        run_id="run-x",
        success=False,
        changed_files=["src/a.py"],
        validation_passed=False,
        blocked_classification="scope_policy",
        validation_results=[],
        branch_pushed=False,
        draft_branch_pushed=False,
        push_reason=None,
        pull_request_url=None,
        summary="fail",
        artifacts=[],
        policy_violations=["src/a.py: out of scope"],
    )
    # run_dir lookup will return None (no reporter) so we skip the summary write
    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.latest_run_dir", lambda r: None
    )
    rewrite_worker_summary(result, service, "TASK-RWS")
    artifact = service.usage_store.get_task_artifact("TASK-RWS")
    assert artifact is not None
    assert artifact["blocked_classification"] == "scope_policy"


# ---------------------------------------------------------------------------
# Item 2: _extract_filename_tokens / _has_conflict_with_active_task
# ---------------------------------------------------------------------------

def test_extract_filename_tokens_finds_py_files() -> None:
    tokens = _extract_filename_tokens("Decompose stage_driver.py (2960L, 17 oversized function(s))")
    assert tokens == {"stage_driver.py"}


def test_extract_filename_tokens_multiple_files() -> None:
    tokens = _extract_filename_tokens("Fix test_git_client.py and client.py")
    assert tokens == {"test_git_client.py", "client.py"}


def test_extract_filename_tokens_no_py_files() -> None:
    tokens = _extract_filename_tokens("Add return type annotations to 142 public functions")
    assert tokens == set()


def test_has_conflict_with_active_task_detects_overlap() -> None:
    issues = [
        {"id": "T1", "state": {"name": "Running"}, "name": "Decompose stage_driver.py (2960L)"},
    ]
    assert _has_conflict_with_active_task("Add tests for stage_driver.py", issues) is True


def test_has_conflict_skips_non_active_tasks() -> None:
    issues = [
        {"id": "T1", "state": {"name": "Done"}, "name": "Decompose stage_driver.py (2960L)"},
    ]
    assert _has_conflict_with_active_task("Add tests for stage_driver.py", issues) is False


def test_has_conflict_no_py_tokens_never_conflicts() -> None:
    issues = [
        {"id": "T1", "state": {"name": "Running"}, "name": "Decompose stage_driver.py"},
    ]
    assert _has_conflict_with_active_task("Add return type annotations to 10 functions", issues) is False


def test_has_conflict_uses_artifact_changed_files_for_review_task() -> None:
    """Review task with artifact should use changed_files, not title tokens."""
    service = FakeService()
    # Review task whose title doesn't mention the file — title-token fallback would miss this
    issues = [
        {"id": "T-REV", "state": {"name": "Review"}, "name": "Add return type annotations"},
    ]
    service.usage_store.record_task_artifact(
        task_id="T-REV",
        artifact={"changed_files": ["src/control_plane/entrypoints/worker/main.py"], "success": True},
        now=datetime(2026, 4, 6, tzinfo=UTC),
    )
    assert _has_conflict_with_active_task("Decompose main.py", issues, service.usage_store) is True


def test_has_conflict_uses_artifact_skips_title_when_artifact_present() -> None:
    """If artifact exists but changed_files don't overlap, title match should not fire."""
    service = FakeService()
    issues = [
        # Title mentions main.py but artifact shows different files
        {"id": "T-RUN", "state": {"name": "Running"}, "name": "Decompose main.py (2800L)"},
    ]
    service.usage_store.record_task_artifact(
        task_id="T-RUN",
        artifact={"changed_files": ["src/other.py"], "success": True},
        now=datetime(2026, 4, 6, tzinfo=UTC),
    )
    # Proposal targets main.py; artifact says task only touched other.py — no conflict
    assert _has_conflict_with_active_task("Fix main.py types", issues, service.usage_store) is False


def test_has_conflict_uses_open_pr_files_for_running_without_artifact() -> None:
    """Running task with no artifact should match via open_pr_files set."""
    issues = [
        {"id": "T-RUN", "state": {"name": "Running"}, "name": "Some unrelated task"},
    ]
    open_pr_files = {"main.py", "client.py"}  # pre-collected from GitHub API
    assert _has_conflict_with_active_task("Decompose main.py", issues, None, open_pr_files) is True


def test_has_conflict_open_pr_files_ignored_for_non_running() -> None:
    """open_pr_files should not cause false positives on Review tasks (artifact path handles those)."""
    issues = [
        {"id": "T-REV", "state": {"name": "Review"}, "name": "Some unrelated task"},
    ]
    open_pr_files = {"main.py"}
    # No artifact, status is Review, open_pr_files only apply to Running
    assert _has_conflict_with_active_task("Decompose main.py", issues, None, open_pr_files) is False


def test_handle_propose_cycle_skips_conflicting_proposals(monkeypatch) -> None:
    """Proposals whose .py filename overlaps with a Running task should be skipped."""
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)

    service = FakeService()
    conflicting_proposal = ProposalSpec(
        task_kind="goal",
        title="Decompose main.py (2823L)",
        goal_text="Decompose main.py",
        reason_summary="oversized file",
        source_signal="repo_scan",
        confidence="high",
        recommended_state="Backlog",
        handoff_reason="scan",
        dedup_key="cp|decompose|main_py",
    )
    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.build_proposal_candidates",
        lambda *a, **kw: ([conflicting_proposal], [], True),
    )
    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main._collect_open_pr_files",
        lambda service: set(),
    )
    client = FakePlaneClient(
        issues=[
            # Running task touching main.py
            {"id": "T-RUN", "state": {"name": "Running"}, "name": "Decompose main.py (2800L)", "labels": []},
        ]
    )
    result = handle_propose_cycle(client, service, now=now)
    # Proposal should be skipped, no new tasks created
    assert result.created_task_ids == []


# ---------------------------------------------------------------------------
# Item 3: _check_task_pr_merged
# ---------------------------------------------------------------------------

def test_check_task_pr_merged_returns_false_when_no_artifact() -> None:
    service = FakeService()
    assert _check_task_pr_merged("NO-TASK", service.usage_store) is False


def test_check_task_pr_merged_returns_false_when_no_pr_url() -> None:
    service = FakeService()
    service.usage_store.record_task_artifact(
        task_id="TASK-1",
        artifact={"pull_request_url": "", "success": True, "changed_files": []},
        now=datetime(2026, 4, 6, tzinfo=UTC),
    )
    assert _check_task_pr_merged("TASK-1", service.usage_store) is False


def test_check_task_pr_merged_returns_false_without_token(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    service = FakeService()
    service.usage_store.record_task_artifact(
        task_id="TASK-2",
        artifact={"pull_request_url": "https://github.com/owner/repo/pull/5", "success": True, "changed_files": []},
        now=datetime(2026, 4, 6, tzinfo=UTC),
    )
    assert _check_task_pr_merged("TASK-2", service.usage_store) is False


def test_check_task_pr_merged_true_when_github_reports_merged(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    service = FakeService()
    service.usage_store.record_task_artifact(
        task_id="TASK-3",
        artifact={"pull_request_url": "https://github.com/owner/repo/pull/7", "success": True, "changed_files": []},
        now=datetime(2026, 4, 6, tzinfo=UTC),
    )

    class FakeGHClient:
        def get_pr(self, owner, repo, pr_number):
            return {"merged": True, "state": "closed", "merged_at": "2026-04-05T10:00:00Z"}

    monkeypatch.setattr("control_plane.entrypoints.worker.main.GitHubPRClient", lambda token: FakeGHClient())
    assert _check_task_pr_merged("TASK-3", service.usage_store) is True


# ---------------------------------------------------------------------------
# Item 4: avoid_paths injection in scope_policy triage
# ---------------------------------------------------------------------------

def test_build_improve_triage_result_injects_avoid_paths_for_scope_policy(monkeypatch) -> None:
    """When scope_policy + artifact with changed_files, constraints should include avoid_paths."""
    import tempfile
    now = datetime(2026, 4, 6, tzinfo=UTC)
    usage_path = Path(tempfile.mkdtemp()) / "usage.json"
    store = UsageStore(usage_path)
    store.record_task_artifact(
        task_id="BLOCKED-1",
        artifact={
            "outcome_status": "executed",
            "changed_files": ["scripts/deploy.sh", "src/main.py"],
            "validation_passed": False,
            "blocked_classification": "scope_policy",
            "pull_request_url": "",
            "success": False,
        },
        now=now,
    )

    # Patch UsageStore() constructor to return our pre-seeded store
    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.UsageStore",
        lambda *args, **kwargs: store,
    )

    issue = {
        "id": "BLOCKED-1",
        "name": "Fix something",
        "labels": [{"name": "task-kind: goal"}],
    }
    comments = [
        {"comment_html": "<p>policy_violations: scripts/deploy.sh</p>"},
    ]
    client = FakePlaneClient(issues=[issue], comments={"BLOCKED-1": comments})
    result = build_improve_triage_result(client, issue, comments, include_failure_context=False)

    assert result.follow_up is not None
    constraints = result.follow_up.constraints_text or ""
    assert "avoid_paths:" in constraints
    assert "scripts/deploy.sh" in constraints
    assert "src/main.py" in constraints


# ===========================================================================
# Tests for the 8 autonomy gap improvements
# ===========================================================================

# ---------------------------------------------------------------------------
# Point 1: Goal coherence — _proposal_matches_focus_areas
# ---------------------------------------------------------------------------

def test_proposal_matches_focus_areas_returns_true_when_no_areas() -> None:
    p = ProposalSpec(
        task_kind="goal", title="Anything", goal_text="Anything",
        reason_summary="", source_signal="", confidence="medium",
        recommended_state="Backlog", handoff_reason="", dedup_key="k",
    )
    assert _proposal_matches_focus_areas(p, []) is True


def test_proposal_matches_focus_areas_matches_title() -> None:
    p = ProposalSpec(
        task_kind="goal", title="Add test coverage for auth module", goal_text="Write tests.",
        reason_summary="", source_signal="", confidence="medium",
        recommended_state="Backlog", handoff_reason="", dedup_key="k",
    )
    assert _proposal_matches_focus_areas(p, ["test coverage"]) is True


def test_proposal_matches_focus_areas_misses_unrelated() -> None:
    p = ProposalSpec(
        task_kind="goal", title="Decompose stage_driver.py", goal_text="Extract large functions.",
        reason_summary="", source_signal="", confidence="medium",
        recommended_state="Backlog", handoff_reason="", dedup_key="k",
    )
    assert _proposal_matches_focus_areas(p, ["test coverage", "type safety"]) is False


def test_build_proposal_candidates_demotes_off_focus_proposals(monkeypatch) -> None:
    """Proposals not matching focus_areas should be demoted to Backlog."""
    from control_plane.entrypoints.worker.main import build_proposal_candidates
    service = FakeService()
    # Give the service focus_areas
    service.settings.focus_areas = ["subprocess safety"]

    off_focus = ProposalSpec(
        task_kind="goal", title="Decompose main.py (2823L)",
        goal_text="Extract large functions from main.py.",
        reason_summary="oversized", source_signal="cp:repo_scan:goal",
        confidence="high", recommended_state="Ready for AI",
        handoff_reason="scan", dedup_key="cp:repo_scan:decompose_main_py",
        repo_key="control-plane",
    )
    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.discover_improvement_candidates",
        lambda *a, **kw: ([], []),
    )
    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main.proposal_specs_from_findings",
        lambda *a, **kw: [off_focus],
    )
    client = FakePlaneClient(issues=[])
    proposals, _, _ = build_proposal_candidates(client, service, repo_key="control-plane")
    assert all(p.recommended_state == "Backlog" for p in proposals if p.title == off_focus.title)


# ---------------------------------------------------------------------------
# Point 2: Task dependency ordering
# ---------------------------------------------------------------------------

def test_parse_task_dependencies_finds_uuid() -> None:
    desc = "## Constraints\n- depends_on: abc12345-1234-1234-1234-abcdef012345"
    deps = parse_task_dependencies(desc)
    assert deps == ["abc12345-1234-1234-1234-abcdef012345"]


def test_parse_task_dependencies_empty_when_no_marker() -> None:
    assert parse_task_dependencies("## Goal\nDo something.") == []


def test_parse_task_dependencies_multiple() -> None:
    desc = "depends_on: aabbccdd-0000-0000-0000-000000000001, aabbccdd-0000-0000-0000-000000000002"
    deps = parse_task_dependencies(desc)
    assert len(deps) == 2


def test_task_dependencies_met_true_when_no_deps() -> None:
    issue = {"id": "T-1", "state": {"name": "Ready for AI"}, "description": "## Goal\nNo deps.", "labels": []}
    client = FakePlaneClient(issues=[issue])
    assert task_dependencies_met(client, "T-1") is True


def test_task_dependencies_met_false_when_dep_not_done() -> None:
    _DEP_UUID = "aabbccdd-0000-0000-0000-000000000001"
    dep = {"id": _DEP_UUID, "state": {"name": "Running"}, "description": "", "labels": []}
    task = {
        "id": "T-2", "state": {"name": "Ready for AI"},
        "description": f"depends_on: {_DEP_UUID}",
        "labels": [],
    }

    class ClientWithDep(FakePlaneClient):
        def fetch_issue(self, task_id):
            if task_id == _DEP_UUID:
                return dep
            return super().fetch_issue(task_id)

    client = ClientWithDep(issues=[task, dep])
    assert task_dependencies_met(client, "T-2") is False


def test_select_watch_candidate_skips_task_with_unmet_deps(monkeypatch) -> None:
    """Tasks with unmet depends_on should be skipped even when Ready for AI."""
    _DEP_UUID = "aabbccdd-0000-0000-0000-000000000002"
    dep = {"id": "DEP-1", "state": {"name": "Running"}, "description": "", "labels": [{"name": "task-kind: goal"}]}
    task = {
        "id": "GOAL-1",
        "state": {"name": "Ready for AI"},
        "description": f"depends_on: {_DEP_UUID}",
        "labels": [{"name": "task-kind: goal"}],
        "name": "Do something",
    }
    task2 = {
        "id": "GOAL-2",
        "state": {"name": "Ready for AI"},
        "description": "",
        "labels": [{"name": "task-kind: goal"}],
        "name": "Do something else",
    }

    class MultiClient(FakePlaneClient):
        def fetch_issue(self, tid):
            if tid == _DEP_UUID:
                return dep
            return super().fetch_issue(tid)

    client = MultiClient(issues=[task, dep, task2])
    task_id, action = select_watch_candidate(client, ready_state="Ready for AI", role="goal")
    assert task_id == "GOAL-2"  # GOAL-1 skipped due to unmet dep


# ---------------------------------------------------------------------------
# Point 3: Task sizing gate — _split_oversized_finding
# ---------------------------------------------------------------------------

def test_split_oversized_finding_passthrough_small() -> None:
    finding = {
        "kind": "goal",
        "title": "Decompose small.py (400L, 3 oversized function(s))",
        "goal": "Decompose small.py",
        "constraints": "- source: size scan",
        "note": "small",
    }
    result = _split_oversized_finding(finding)
    assert len(result) == 1
    assert result[0]["title"] == finding["title"]


def test_split_oversized_finding_splits_large() -> None:
    finding = {
        "kind": "goal",
        "title": "Decompose main.py (2823L, 16 oversized function(s))",
        "goal": "Decompose main.py",
        "constraints": "- source: size scan",
        "note": "oversized",
    }
    result = _split_oversized_finding(finding)
    assert len(result) >= 2
    for i, part in enumerate(result, 1):
        assert f"part {i} of {len(result)}" in part["title"]
        assert "decomposition_part:" in part["constraints"]


def test_split_oversized_finding_non_decompose_passthrough() -> None:
    finding = {
        "kind": "goal",
        "title": "Fix 212 type errors found by ty",
        "goal": "Fix types",
        "constraints": "",
        "note": "",
    }
    assert _split_oversized_finding(finding) == [finding]


# ---------------------------------------------------------------------------
# Point 4+6: Post-merge CI feedback — detect_post_merge_regressions
# ---------------------------------------------------------------------------

def test_detect_post_merge_regressions_no_token_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    service = FakeService()
    # settings.git_token() returns None when no token
    service.settings.git_token = lambda: None
    client = FakePlaneClient(issues=[{"id": "T-1", "state": {"name": "Done"}, "labels": []}])
    assert detect_post_merge_regressions(client, service) == []


def test_detect_post_merge_regressions_skips_tasks_without_artifact() -> None:
    service = FakeService()
    service.settings.git_token = lambda: "fake-token"
    client = FakePlaneClient(issues=[{"id": "T-1", "state": {"name": "Done"}, "labels": [], "name": "Task 1"}])
    assert detect_post_merge_regressions(client, service) == []


def test_detect_post_merge_regressions_creates_task_on_ci_failure(monkeypatch) -> None:
    service = FakeService()
    service.settings.git_token = lambda: "fake-token"
    now = datetime(2026, 4, 6, tzinfo=UTC)
    service.usage_store.record_task_artifact(
        task_id="DONE-1",
        artifact={
            "pull_request_url": "https://github.com/owner/repo/pull/10",
            "success": True, "changed_files": [],
        },
        now=now,
    )

    class FakeGH:
        def get_pr(self, owner, repo, pr_number):
            return {"merged": True, "merged_at": "2026-04-05T10:00:00Z", "head": {"sha": "abc123"}, "state": "closed"}
        def get_failed_checks(self, owner, repo, pr_number, **kw):
            return ["tests: FAILED"]

    monkeypatch.setattr("control_plane.entrypoints.worker.main.GitHubPRClient", lambda token: FakeGH())

    issue = {"id": "DONE-1", "state": {"name": "Done"}, "labels": [], "name": "Implement feature X"}
    client = FakePlaneClient(issues=[issue])
    created = detect_post_merge_regressions(client, service, issues=[issue])
    assert len(created) == 1
    assert any("Regression from" in c.get("name", "") for c in client.created)


def test_detect_post_merge_regressions_skips_if_already_flagged(monkeypatch) -> None:
    service = FakeService()
    service.settings.git_token = lambda: "fake-token"
    now = datetime(2026, 4, 6, tzinfo=UTC)
    service.usage_store.record_task_artifact(
        task_id="DONE-2",
        artifact={"pull_request_url": "https://github.com/owner/repo/pull/11", "success": True, "changed_files": []},
        now=now,
    )
    existing_marker_comment = {"comment_html": "<p>[Improve] Post-merge regression detected</p>"}
    issue = {"id": "DONE-2", "state": {"name": "Done"}, "labels": [], "name": "Some task"}
    client = FakePlaneClient(issues=[issue], comments={"DONE-2": [existing_marker_comment]})
    assert detect_post_merge_regressions(client, service, issues=[issue]) == []


# ---------------------------------------------------------------------------
# Point 5: Self-modification controls
# ---------------------------------------------------------------------------

def test_is_self_repo_matches_self_repo_key() -> None:
    service = FakeService()
    service.settings.self_repo_key = "ControlPlane"
    assert _is_self_repo("ControlPlane", service) is True
    assert _is_self_repo("code_youtube_shorts", service) is False


def test_is_self_repo_case_insensitive() -> None:
    service = FakeService()
    service.settings.self_repo_key = "ControlPlane"
    assert _is_self_repo("controlplane", service) is True


def test_self_modify_approved_detects_label() -> None:
    issue_with = {"labels": [{"name": "self-modify: approved"}]}
    issue_without = {"labels": [{"name": "task-kind: goal"}]}
    assert _self_modify_approved(issue_with) is True
    assert _self_modify_approved(issue_without) is False


def test_select_watch_candidate_skips_self_repo_without_label() -> None:
    """Self-repo tasks without 'self-modify: approved' must be skipped."""
    task_self = {
        "id": "SELF-1", "state": {"name": "Ready for AI"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: ControlPlane"}],
        "name": "Decompose main.py",
        "description": "",
    }
    task_other = {
        "id": "OTHER-1", "state": {"name": "Ready for AI"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: code_youtube_shorts"}],
        "name": "Fix something",
        "description": "",
    }
    service = FakeService()
    service.settings.self_repo_key = "ControlPlane"
    client = FakePlaneClient(issues=[task_self, task_other])
    task_id, _ = select_watch_candidate(
        client, ready_state="Ready for AI", role="goal", service=service
    )
    assert task_id == "OTHER-1"


def test_select_watch_candidate_allows_self_repo_with_approved_label() -> None:
    task_self = {
        "id": "SELF-2", "state": {"name": "Ready for AI"},
        "labels": [
            {"name": "task-kind: goal"},
            {"name": "repo: ControlPlane"},
            {"name": "self-modify: approved"},
        ],
        "name": "Decompose main.py",
        "description": "",
    }
    service = FakeService()
    service.settings.self_repo_key = "ControlPlane"
    client = FakePlaneClient(issues=[task_self])
    task_id, _ = select_watch_candidate(
        client, ready_state="Ready for AI", role="goal", service=service
    )
    assert task_id == "SELF-2"


# ---------------------------------------------------------------------------
# Point 7: Better failure attribution
# ---------------------------------------------------------------------------

def test_classify_execution_result_context_limit() -> None:
    result = ExecutionResult(
        run_id="r", success=False, changed_files=[], validation_passed=False,
        execution_stderr_excerpt="context window exceeded: token limit reached",
        summary="fail", validation_results=[], branch_pushed=False,
        draft_branch_pushed=False, push_reason=None, pull_request_url=None,
        artifacts=[], policy_violations=[],
    )
    assert classify_execution_result(result) == "context_limit"


def test_classify_execution_result_dependency_missing() -> None:
    result = ExecutionResult(
        run_id="r", success=False, changed_files=[], validation_passed=False,
        execution_stderr_excerpt="ModuleNotFoundError: No module named 'httpx'",
        summary="fail", validation_results=[], branch_pushed=False,
        draft_branch_pushed=False, push_reason=None, pull_request_url=None,
        artifacts=[], policy_violations=[],
    )
    assert classify_execution_result(result) == "dependency_missing"


def test_classify_blocked_issue_context_limit() -> None:
    issue = {"name": "Fix something", "description": "", "labels": [{"name": "task-kind: goal"}]}
    comments = [{"comment_html": "<p>blocked_classification: context_limit</p>"}]
    classification, _ = classify_blocked_issue(issue, comments)
    assert classification == "context_limit"


def test_build_improve_triage_context_limit_gets_follow_up() -> None:
    """context_limit should produce a scoping follow-up, not human_attention."""
    issue = {"id": "T-CL", "name": "Decompose main.py", "labels": [{"name": "task-kind: goal"}]}
    comments = [{"comment_html": "<p>blocked_classification: context_limit</p>"}]
    client = FakePlaneClient(issues=[issue], comments={"T-CL": comments})
    result = build_improve_triage_result(client, issue, comments, include_failure_context=False)
    assert result.classification == "context_limit"
    assert result.human_attention_required is False
    assert result.follow_up is not None


# ---------------------------------------------------------------------------
# Point 8: Satiation signal
# ---------------------------------------------------------------------------

def test_is_proposal_satiated_false_with_insufficient_data() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    # Only 3 cycles recorded, need 5
    for _ in range(3):
        service.usage_store.record_proposal_cycle(created=0, deduped=4, skipped=0, now=now)
    assert service.usage_store.is_proposal_satiated(now=now) is False


def test_is_proposal_satiated_true_when_all_deduped() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    for _ in range(5):
        service.usage_store.record_proposal_cycle(created=0, deduped=5, skipped=0, now=now)
    assert service.usage_store.is_proposal_satiated(now=now) is True


def test_is_proposal_satiated_false_when_creating_tasks() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    for _ in range(4):
        service.usage_store.record_proposal_cycle(created=0, deduped=5, skipped=0, now=now)
    service.usage_store.record_proposal_cycle(created=2, deduped=1, skipped=0, now=now)
    assert service.usage_store.is_proposal_satiated(now=now) is False


def test_handle_propose_cycle_returns_satiated_when_store_satiated(monkeypatch) -> None:
    """handle_propose_cycle should short-circuit with 'satiated' decision."""
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    service = FakeService()
    # Seed 5 all-deduped cycles so is_proposal_satiated returns True
    for _ in range(5):
        service.usage_store.record_proposal_cycle(created=0, deduped=5, skipped=0, now=now)
    client = FakePlaneClient(issues=[])
    result = handle_propose_cycle(client, service, now=now)
    assert result.decision == "satiated"


# ---------------------------------------------------------------------------
# New autonomy improvements — tests
# ---------------------------------------------------------------------------

# Item 1: Human escalation channel

def test_usage_store_should_escalate_fires_after_threshold() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    service = FakeService()
    # Record 5 blocked_triage events with same classification within 24h
    for i in range(5):
        service.usage_store.record_blocked_triage(
            task_id=f"T-{i}", classification="validation_failure", now=now
        )
    should, ids = service.usage_store.should_escalate(
        classification="validation_failure",
        threshold=5,
        cooldown_seconds=3600,
        now=now,
    )
    assert should is True
    assert len(ids) == 5


def test_usage_store_should_escalate_false_below_threshold() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    service = FakeService()
    for i in range(3):
        service.usage_store.record_blocked_triage(
            task_id=f"T-{i}", classification="validation_failure", now=now
        )
    should, _ = service.usage_store.should_escalate(
        classification="validation_failure",
        threshold=5,
        cooldown_seconds=3600,
        now=now,
    )
    assert should is False


def test_usage_store_should_escalate_false_during_cooldown() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    service = FakeService()
    for i in range(6):
        service.usage_store.record_blocked_triage(
            task_id=f"T-{i}", classification="validation_failure", now=now
        )
    # Record that we already escalated 30 min ago (within cooldown)
    from datetime import timedelta
    service.usage_store.record_escalation(
        classification="validation_failure",
        task_ids=["T-0"],
        now=now - timedelta(minutes=30),
    )
    should, _ = service.usage_store.should_escalate(
        classification="validation_failure",
        threshold=5,
        cooldown_seconds=3600,
        now=now,
    )
    assert should is False


# Item 3: Watcher heartbeat monitoring

def test_write_and_check_heartbeat_healthy(tmp_path) -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    write_heartbeat(tmp_path, "goal", now=now)
    # Check immediately — should be healthy
    stale = check_heartbeats(tmp_path, now=now)
    assert stale == []


def test_check_heartbeats_detects_stale(tmp_path) -> None:
    from datetime import timedelta
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    old = now - timedelta(minutes=10)
    write_heartbeat(tmp_path, "goal", now=old)
    stale = check_heartbeats(tmp_path, now=now)
    assert "goal" in stale


def test_check_heartbeats_empty_dir(tmp_path) -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    stale = check_heartbeats(tmp_path, now=now)
    assert stale == []


def test_heartbeat_path_naming(tmp_path) -> None:
    p = _heartbeat_path(tmp_path, "improve")
    assert p.name == "heartbeat_improve.json"


# Item 4: Context handoff — prior_progress injection

def test_context_limit_goal_text_includes_prior_progress() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    service = FakeService()
    task_id = "TASK-CTX"
    service.usage_store.record_task_artifact(
        task_id=task_id,
        artifact={"summary": "completed steps 1-3 of refactor"},
        now=now,
    )
    issue = {
        "id": task_id,
        "name": "Decompose big.py",
        "labels": [{"name": "task-kind: goal"}],
        "description": "blocked_classification: context_limit",
    }
    comments = [{"comment_html": "<p>blocked_classification: context_limit</p>"}]
    client = FakePlaneClient(issues=[issue])
    result = build_improve_triage_result(client, issue, comments, usage_store=service.usage_store)
    assert result.follow_up is not None
    assert "prior_progress" in result.follow_up.goal_text
    assert "completed steps 1-3" in result.follow_up.goal_text


def test_context_limit_goal_text_no_prior_progress_when_no_artifact() -> None:
    issue = {
        "id": "TASK-NO-ART",
        "name": "Decompose big.py",
        "labels": [{"name": "task-kind: goal"}],
        "description": "",
    }
    comments = [{"comment_html": "<p>blocked_classification: context_limit</p>"}]
    client = FakePlaneClient(issues=[issue])
    result = build_improve_triage_result(client, issue, comments, usage_store=FakeService().usage_store)
    assert result.follow_up is not None
    assert "prior_progress" not in result.follow_up.goal_text


# Item 5: Flaky test detection

def test_is_command_flaky_returns_false_insufficient_data() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    # Only 5 samples, need 10
    for _ in range(5):
        service.usage_store.record_validation_outcome(command="pytest -q", passed=False, now=now)
    assert service.usage_store.is_command_flaky("pytest -q", now=now) is False


def test_is_command_flaky_returns_true_when_frequently_fails() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    # 4 failures out of 10 = 40% > 30% threshold
    for _ in range(6):
        service.usage_store.record_validation_outcome(command="pytest -q", passed=True, now=now)
    for _ in range(4):
        service.usage_store.record_validation_outcome(command="pytest -q", passed=False, now=now)
    assert service.usage_store.is_command_flaky("pytest -q", now=now) is True


def test_is_command_flaky_returns_false_when_mostly_passing() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    # 1 failure out of 10 = 10% < 30%
    for _ in range(9):
        service.usage_store.record_validation_outcome(command="pytest -q", passed=True, now=now)
    service.usage_store.record_validation_outcome(command="pytest -q", passed=False, now=now)
    assert service.usage_store.is_command_flaky("pytest -q", now=now) is False


def test_classify_execution_result_flaky_test() -> None:
    """classify_execution_result returns flaky_test when command is known flaky."""
    from control_plane.domain.models import ValidationResult

    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    # Make the command known-flaky
    for _ in range(6):
        service.usage_store.record_validation_outcome(command=".venv/bin/pytest", passed=True, now=now)
    for _ in range(4):
        service.usage_store.record_validation_outcome(command=".venv/bin/pytest", passed=False, now=now)
    result = ExecutionResult(
        run_id="r", success=False, changed_files=[], validation_passed=False,
        execution_stderr_excerpt="assert failed",
        summary="fail", validation_results=[
            ValidationResult(command=".venv/bin/pytest", exit_code=1, stdout="", stderr="", duration_ms=0)
        ],
        branch_pushed=False, draft_branch_pushed=False, push_reason=None,
        pull_request_url=None, artifacts=[], policy_violations=[],
    )
    assert classify_execution_result(result, service.usage_store) == "flaky_test"


# Item 7: Token/credential validation

def test_validate_credentials_returns_true_on_401(monkeypatch) -> None:
    """validate_credentials returns False when GitHub returns 401."""
    service = FakeService()
    service.settings.git_token = lambda: "fake-token"
    service.settings.plane_token = lambda: "fake-plane-token"
    service.settings.plane = SimpleNamespace(
        base_url="http://plane.test", workspace_slug="ws"
    )

    responses = [
        httpx.Response(401, request=httpx.Request("GET", "https://api.github.com/user")),
        httpx.Response(200, request=httpx.Request("GET", "http://plane.test/api/v1/workspaces/ws/")),
    ]
    call_count = 0

    def fake_get(url, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)
    result = validate_credentials(
        service.settings, usage_store=service.usage_store, now=datetime(2026, 4, 6, tzinfo=UTC)
    )
    assert result is False


def test_validate_credentials_returns_true_when_all_ok(monkeypatch) -> None:
    service = FakeService()
    service.settings.git_token = lambda: "fake-token"
    service.settings.plane_token = lambda: "fake-plane-token"
    service.settings.plane = SimpleNamespace(
        base_url="http://plane.test", workspace_slug="ws"
    )

    def fake_get(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    result = validate_credentials(
        service.settings, usage_store=service.usage_store, now=datetime(2026, 4, 6, tzinfo=UTC)
    )
    assert result is True


# Item 8: Success/failure learning

def test_proposal_success_rate_neutral_with_few_samples() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    service.usage_store.record_proposal_outcome(category="goal", succeeded=True, now=now)
    rate = service.usage_store.proposal_success_rate("goal", now=now)
    assert rate == 0.5  # < 3 samples → neutral


def test_proposal_success_rate_high() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    for _ in range(8):
        service.usage_store.record_proposal_outcome(category="goal", succeeded=True, now=now)
    for _ in range(2):
        service.usage_store.record_proposal_outcome(category="goal", succeeded=False, now=now)
    rate = service.usage_store.proposal_success_rate("goal", now=now)
    assert rate == 0.8


def test_proposal_success_rate_category_isolation() -> None:
    """Rates for different categories don't bleed into each other."""
    service = FakeService()
    now = datetime(2026, 4, 6, tzinfo=UTC)
    for _ in range(5):
        service.usage_store.record_proposal_outcome(category="goal", succeeded=True, now=now)
    for _ in range(5):
        service.usage_store.record_proposal_outcome(category="test", succeeded=False, now=now)
    assert service.usage_store.proposal_success_rate("goal", now=now) == 1.0
    assert service.usage_store.proposal_success_rate("test", now=now) == 0.0


# Item 9: Scheduled tasks

def test_scheduled_tasks_due_no_croniter(monkeypatch) -> None:
    """_scheduled_tasks_due returns [] when croniter is not installed."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "croniter":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    now = datetime(2026, 4, 6, 9, 0, tzinfo=UTC)
    task = SimpleNamespace(cron="0 9 * * *", title="Weekly check", goal="check", repo_key="r", kind="goal")
    result = _scheduled_tasks_due([task], set(), now=now)
    assert result == []


def test_pr_number_from_url() -> None:
    assert _pr_number_from_url("https://github.com/owner/repo/pull/42") == 42
    assert _pr_number_from_url("https://github.com/owner/repo/pull/42/files") is None
    assert _pr_number_from_url("https://github.com/owner/repo") is None


# Item 2+10: Merge conflict helpers

def test_get_mergeable_returns_none_on_error(monkeypatch) -> None:
    from control_plane.adapters.github_pr import GitHubPRClient

    gh = GitHubPRClient("token")

    def boom(*a, **k):
        raise httpx.NetworkError("down")

    monkeypatch.setattr(httpx, "get", boom)
    result = gh.get_mergeable("owner", "repo", 1)
    assert result is None


# ===========================================================================
# Session 3 — 8 Full-Autonomy Gap Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Item 1: GitHub rate-limit handling
# ---------------------------------------------------------------------------

def test_github_request_retries_on_429(monkeypatch) -> None:
    """_request retries up to _GH_RATE_LIMIT_MAX_RETRIES times on 429."""
    from control_plane.adapters.github_pr import GitHubPRClient
    import httpx as _httpx

    gh = GitHubPRClient("token")
    call_count = 0

    def fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return _httpx.Response(
                429,
                headers={"Retry-After": "0"},
                request=_httpx.Request(method, url),
            )
        return _httpx.Response(200, json={"ok": True}, request=_httpx.Request(method, url))

    monkeypatch.setattr(_httpx, "request", fake_request)
    # Silence the sleep so test runs fast
    monkeypatch.setattr("control_plane.adapters.github_pr.time.sleep", lambda _: None)
    resp = gh._request("GET", "https://api.github.com/user")
    assert resp.status_code == 200
    assert call_count == 3


def test_github_request_warns_on_low_remaining(monkeypatch, caplog) -> None:
    """_request logs a warning when X-RateLimit-Remaining < threshold."""
    import logging
    from control_plane.adapters.github_pr import GitHubPRClient
    import httpx as _httpx

    gh = GitHubPRClient("token")

    def fake_request(method, url, **kwargs):
        return _httpx.Response(
            200,
            json={},
            headers={"X-RateLimit-Remaining": "3"},
            request=_httpx.Request(method, url),
        )

    monkeypatch.setattr(_httpx, "request", fake_request)
    with caplog.at_level(logging.WARNING, logger="control_plane.adapters.github_pr"):
        gh._request("GET", "https://api.github.com/user")
    assert any("github_rate_limit_low" in r.message for r in caplog.records)


def test_github_request_no_warn_on_high_remaining(monkeypatch, caplog) -> None:
    """No warning is emitted when X-RateLimit-Remaining is comfortable."""
    import logging
    from control_plane.adapters.github_pr import GitHubPRClient
    import httpx as _httpx

    gh = GitHubPRClient("token")

    def fake_request(method, url, **kwargs):
        return _httpx.Response(
            200,
            json={},
            headers={"X-RateLimit-Remaining": "500"},
            request=_httpx.Request(method, url),
        )

    monkeypatch.setattr(_httpx, "request", fake_request)
    with caplog.at_level(logging.WARNING, logger="control_plane.adapters.github_pr"):
        gh._request("GET", "https://api.github.com/user")
    assert not any("github_rate_limit_low" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Item 2: Pre-execution task validation
# ---------------------------------------------------------------------------

def test_validate_task_pre_execution_passes_with_good_goal() -> None:
    issue = {"id": "T-1", "state": {"name": "Ready for AI"}, "description": ""}
    client = FakePlaneClient([issue])
    service = FakeService()
    # Service.parse_task will raise AttributeError; fallback to description
    # Give a description long enough to pass
    issue["description"] = "Refactor the authentication module to use JWT tokens instead of sessions."
    # parse_task not on FakeService → falls back to description field
    result = validate_task_pre_execution(client, service, "T-1", issue)
    assert result is True


def test_validate_task_pre_execution_passes_empty_goal() -> None:
    """Empty goal passes (conservative — we cannot determine it's bad)."""
    issue = {"id": "T-1", "state": {"name": "Ready for AI"}}
    client = FakePlaneClient([issue])
    service = FakeService()
    result = validate_task_pre_execution(client, service, "T-1", issue)
    assert result is True


def test_validate_task_pre_execution_rejects_vague_goal() -> None:
    issue = {
        "id": "T-1",
        "state": {"name": "Ready for AI"},
        "description": "fix everything in the codebase please",
    }
    client = FakePlaneClient([issue])
    service = FakeService()
    result = validate_task_pre_execution(client, service, "T-1", issue)
    assert result is False
    # Task should have been moved to Backlog
    assert ("T-1", "Backlog") in client.transitions


def test_validate_task_pre_execution_rejects_too_short() -> None:
    issue = {
        "id": "T-2",
        "state": {"name": "Ready for AI"},
        "description": "fix it",
    }
    client = FakePlaneClient([issue])
    service = FakeService()
    result = validate_task_pre_execution(client, service, "T-2", issue)
    assert result is False


# ---------------------------------------------------------------------------
# Item 3: Feedback loop automation
# ---------------------------------------------------------------------------

def test_handle_feedback_loop_scan_records_merged_pr(tmp_path, monkeypatch) -> None:
    """handle_feedback_loop_scan writes a merged feedback record for a Done task."""
    import httpx as _httpx

    task_id = "aabbccdd-0000-0000-0000-000000000099"
    issue = {
        "id": task_id,
        "state": {"name": "Done"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: control-plane"}],
    }
    client = FakePlaneClient([issue])
    service = FakeService()
    service.settings.git_token = lambda: "gh-token"
    service.settings.repos["control-plane"] = SimpleNamespace(
        clone_url="git@github.com:Owner/Repo.git",
        default_branch="main",
    )
    # Store artifact with a PR URL
    pr_url = "https://github.com/Owner/Repo/pull/7"
    service.usage_store.record_task_artifact(
        task_id=task_id,
        artifact={"pull_request_url": pr_url, "repo_key": "control-plane"},
        now=datetime(2026, 4, 6, tzinfo=UTC),
    )

    def fake_request(method, url, **kwargs):
        return _httpx.Response(
            200,
            json={"state": "closed", "merged_at": "2026-04-06T10:00:00Z", "merged": True},
            request=_httpx.Request(method, url),
        )

    monkeypatch.setattr(_httpx, "request", fake_request)
    monkeypatch.setattr("control_plane.adapters.github_pr.time.sleep", lambda _: None)

    feedback_dir = tmp_path / "state" / "proposal_feedback"
    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main._FEEDBACK_DIR",
        feedback_dir,
    )

    recorded = handle_feedback_loop_scan(
        client, service, now=datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    )
    assert task_id in recorded
    feedback_file = feedback_dir / f"{task_id}.json"
    assert feedback_file.exists()
    import json as _json
    data = _json.loads(feedback_file.read_text())
    assert data["outcome"] == "merged"
    assert data["source"] == "feedback_loop_scan"


def test_handle_feedback_loop_scan_skips_already_recorded(tmp_path, monkeypatch) -> None:
    """handle_feedback_loop_scan skips tasks that already have a feedback file."""
    task_id = "aabbccdd-0000-0000-0000-000000000098"
    issue = {"id": task_id, "state": {"name": "Done"}, "labels": []}
    client = FakePlaneClient([issue])
    service = FakeService()

    feedback_dir = tmp_path / "state" / "proposal_feedback"
    feedback_dir.mkdir(parents=True)
    (feedback_dir / f"{task_id}.json").write_text('{"outcome":"merged"}')

    monkeypatch.setattr(
        "control_plane.entrypoints.worker.main._FEEDBACK_DIR",
        feedback_dir,
    )
    recorded = handle_feedback_loop_scan(client, service)
    assert task_id not in recorded


# ---------------------------------------------------------------------------
# Item 4: Workspace health monitoring
# ---------------------------------------------------------------------------

def test_handle_workspace_health_check_healthy_repo(tmp_path) -> None:
    """No tasks created when the venv python is healthy."""
    # Create a fake python binary that exits 0
    fake_venv = tmp_path / ".venv" / "bin"
    fake_venv.mkdir(parents=True)
    fake_python = fake_venv / "python"
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)

    issue = {"id": "T-WH", "state": {"name": "Ready for AI"}, "labels": []}
    client = FakePlaneClient([issue])
    service = FakeService()
    service.settings.repos["control-plane"] = SimpleNamespace(
        clone_url="git@github.com:Owner/Repo.git",
        default_branch="main",
        local_path=str(tmp_path),
        venv_dir=".venv",
        python_binary="python3",
        install_dev_command=None,
        bootstrap_enabled=False,
        bootstrap_commands=None,
    )

    created = handle_workspace_health_check(client, service)
    assert created == []


def test_handle_workspace_health_check_no_local_path() -> None:
    """Repos without local_path are skipped silently."""
    issue = {"id": "T-X", "state": {"name": "Ready for AI"}, "labels": []}
    client = FakePlaneClient([issue])
    service = FakeService()
    # Default FakeService repo has no local_path
    created = handle_workspace_health_check(client, service)
    assert created == []


# ---------------------------------------------------------------------------
# Item 5: Config schema drift detection
# ---------------------------------------------------------------------------

def test_detect_config_drift_no_gaps(tmp_path) -> None:
    from control_plane.config.drift import detect_config_drift

    config = tmp_path / "config.yaml"
    example = tmp_path / "example.yaml"
    config.write_text("plane:\n  base_url: x\nescalation:\n  webhook_url: ''\n")
    example.write_text("plane:\n  base_url: x\nescalation:\n  webhook_url: ''\n")
    assert detect_config_drift(config, example) == []


def test_detect_config_drift_missing_top_level(tmp_path) -> None:
    from control_plane.config.drift import detect_config_drift

    config = tmp_path / "config.yaml"
    example = tmp_path / "example.yaml"
    config.write_text("plane:\n  base_url: x\n")
    example.write_text("plane:\n  base_url: x\nescalation:\n  webhook_url: ''\n")
    gaps = detect_config_drift(config, example)
    assert "escalation" in gaps


def test_detect_config_drift_missing_nested_key(tmp_path) -> None:
    from control_plane.config.drift import detect_config_drift

    config = tmp_path / "config.yaml"
    example = tmp_path / "example.yaml"
    config.write_text("escalation:\n  webhook_url: ''\n")
    example.write_text("escalation:\n  webhook_url: ''\n  block_threshold: 5\n")
    gaps = detect_config_drift(config, example)
    assert "escalation.block_threshold" in gaps


def test_detect_config_drift_missing_files_returns_empty(tmp_path) -> None:
    from control_plane.config.drift import detect_config_drift

    gaps = detect_config_drift(tmp_path / "nonexistent.yaml", tmp_path / "also_missing.yaml")
    assert gaps == []


# ---------------------------------------------------------------------------
# Item 6: Cost/spend telemetry
# ---------------------------------------------------------------------------

def test_record_execution_cost_and_get_report() -> None:
    service = FakeService()
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    service.usage_store.record_execution_cost(
        task_id="T-1", repo_key="repo-a", estimated_usd=0.05, now=now
    )
    service.usage_store.record_execution_cost(
        task_id="T-2", repo_key="repo-a", estimated_usd=0.10, now=now
    )
    service.usage_store.record_execution_cost(
        task_id="T-3", repo_key="repo-b", estimated_usd=0.02, now=now
    )
    report = service.usage_store.get_spend_report(window_days=1, now=now)
    assert report["total_executions"] == 3
    assert abs(report["total_estimated_usd"] - 0.17) < 1e-6
    assert report["per_repo"]["repo-a"]["executions"] == 2
    assert abs(report["per_repo"]["repo-a"]["estimated_usd"] - 0.15) < 1e-6
    assert report["per_repo"]["repo-b"]["executions"] == 1


def test_get_spend_report_outside_window() -> None:
    """Events older than window_days are excluded."""
    service = FakeService()
    old = datetime(2026, 3, 1, tzinfo=UTC)
    now = datetime(2026, 4, 6, tzinfo=UTC)
    service.usage_store.record_execution_cost(
        task_id="T-old", repo_key="r", estimated_usd=9.99, now=old
    )
    report = service.usage_store.get_spend_report(window_days=1, now=now)
    assert report["total_executions"] == 0
    assert report["total_estimated_usd"] == 0.0


# ---------------------------------------------------------------------------
# Item 7: Parallel execution
# ---------------------------------------------------------------------------

def test_run_parallel_watch_loop_single_slot() -> None:
    """With n_slots=1, parallel loop runs one task exactly like run_watch_loop."""
    client = FakePlaneClient(
        [{"id": "TASK-P1", "state": {"name": "Ready for AI"}, "labels": [{"name": "task-kind: goal"}]}]
    )
    service = FakeService()
    run_parallel_watch_loop(
        client,
        service,
        role="goal",
        ready_state="Ready for AI",
        poll_interval_seconds=0,
        max_cycles=1,
        n_slots=1,
    )
    assert ("TASK-P1", "Running") in client.transitions


def test_run_parallel_watch_loop_two_slots(tmp_path) -> None:
    """Two parallel slots both attempt to claim tasks and at least one succeeds."""

    # Give enough tasks for both slots
    tasks = [
        {"id": f"T-{i}", "state": {"name": "Ready for AI"}, "labels": [{"name": "task-kind: goal"}]}
        for i in range(4)
    ]
    client = FakePlaneClient(tasks)
    service = FakeService()

    run_parallel_watch_loop(
        client,
        service,
        role="goal",
        ready_state="Ready for AI",
        poll_interval_seconds=0,
        max_cycles=2,
        n_slots=2,
    )
    # At least one transition to Running happened
    running_transitions = [t for t in client.transitions if t[1] == "Running"]
    assert len(running_transitions) >= 1


# ---------------------------------------------------------------------------
# Item 8: Multi-step dependency planning
# ---------------------------------------------------------------------------

def test_is_multi_step_task_keyword_in_title() -> None:
    for kw in ("refactor", "migrate", "redesign"):
        issue = {"id": "T", "name": f"Please {kw} the auth module", "labels": []}
        assert _is_multi_step_task(issue), f"keyword '{kw}' should trigger multi-step"


def test_is_multi_step_task_explicit_label() -> None:
    issue = {"id": "T", "name": "Update logging", "labels": [{"name": "plan: multi-step"}]}
    assert _is_multi_step_task(issue)


def test_is_multi_step_task_normal_task_is_false() -> None:
    issue = {"id": "T", "name": "Fix typo in README", "labels": [{"name": "task-kind: goal"}]}
    assert not _is_multi_step_task(issue)


def test_build_multi_step_plan_creates_three_steps() -> None:
    issue = {
        "id": "SOURCE-1",
        "name": "Refactor authentication module",
        "state": {"name": "Ready for AI"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: control-plane"}],
        "description": "## Execution\nrepo: control-plane\nmode: goal\n\n## Goal\nRefactor auth.\n",
    }
    client = FakePlaneClient([issue])
    service = FakeService()

    created = build_multi_step_plan(client, service, "SOURCE-1", issue)
    assert len(created) == 3
    # Step titles should include step numbers
    names = [c["name"] for c in client.created]
    assert any("Step 1/3" in n for n in names)
    assert any("Step 2/3" in n for n in names)
    assert any("Step 3/3" in n for n in names)
    # Source task should be moved to Backlog
    assert ("SOURCE-1", "Backlog") in client.transitions


def test_build_multi_step_plan_skips_non_complex_task() -> None:
    issue = {
        "id": "SIMPLE-1",
        "name": "Fix typo in docstring",
        "state": {"name": "Ready for AI"},
        "labels": [{"name": "task-kind: goal"}],
    }
    client = FakePlaneClient([issue])
    service = FakeService()
    created = build_multi_step_plan(client, service, "SIMPLE-1", issue)
    assert created == []
    assert client.created == []


def test_build_multi_step_plan_skips_if_steps_exist() -> None:
    """Plan is not recreated if step tasks are already on the board."""
    issue = {
        "id": "SOURCE-2",
        "name": "Redesign database layer",
        "state": {"name": "Ready for AI"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: control-plane"}],
    }
    existing_step = {
        "id": "STEP-EXISTING",
        "name": "[Step 1/3: Analyze] Redesign database layer",
        "state": {"name": "Ready for AI"},
        "labels": [],
    }
    client = FakePlaneClient([issue, existing_step])
    service = FakeService()
    created = build_multi_step_plan(client, service, "SOURCE-2", issue)
    assert created == []

# ---------------------------------------------------------------------------
# Session 5 tests
# ---------------------------------------------------------------------------

# ---- Circuit breaker ----

def test_budget_decision_circuit_breaker_opens_on_high_failure_rate() -> None:
    """Circuit breaker blocks when ≥80% of last 5 outcomes failed."""
    import os
    os.environ["CONTROL_PLANE_CIRCUIT_BREAKER_THRESHOLD"] = "0.8"
    os.environ["CONTROL_PLANE_CIRCUIT_BREAKER_WINDOW"] = "5"
    store = UsageStore(Path(tempfile.mkdtemp()) / "usage.json")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(4):
        store.record_execution_outcome(task_id=f"T-{i}", role="goal", succeeded=False, now=now)
    store.record_execution_outcome(task_id="T-4", role="goal", succeeded=True, now=now)
    decision = store.budget_decision(now=now)
    assert not decision.allowed
    assert decision.reason == "circuit_breaker_open"


def test_budget_decision_circuit_breaker_stays_open_below_minimum_samples() -> None:
    """Circuit breaker requires ≥3 samples before triggering."""
    store = UsageStore(Path(tempfile.mkdtemp()) / "usage.json")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(2):
        store.record_execution_outcome(task_id=f"T-{i}", role="goal", succeeded=False, now=now)
    decision = store.budget_decision(now=now)
    assert decision.allowed  # Only 2 samples, should not trigger


def test_budget_decision_circuit_breaker_does_not_fire_on_mixed_outcomes() -> None:
    """Circuit breaker stays open when success rate is acceptable (40% success = 60% fail)."""
    import os
    os.environ["CONTROL_PLANE_CIRCUIT_BREAKER_THRESHOLD"] = "0.8"
    store = UsageStore(Path(tempfile.mkdtemp()) / "usage.json")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(3):
        store.record_execution_outcome(task_id=f"F-{i}", role="goal", succeeded=False, now=now)
    for i in range(2):
        store.record_execution_outcome(task_id=f"S-{i}", role="goal", succeeded=True, now=now)
    decision = store.budget_decision(now=now)
    # 3/5 = 60% failure, below 80% threshold
    assert decision.allowed


# ---- Stale autonomy task scan ----

def test_handle_stale_autonomy_task_scan_cancels_stale_task() -> None:
    """Tasks with source:autonomy in Backlog older than stale_days are cancelled."""
    from datetime import timedelta, timezone as _tz
    stale_created = (datetime.now(_tz.utc) - timedelta(days=25)).isoformat()
    issue = {
        "id": "AUTO-1",
        "name": "Fix lint issues",
        "state": {"name": "Backlog"},
        "labels": [{"name": "source: autonomy"}, {"name": "task-kind: goal"}],
        "created_at": stale_created,
    }
    client = FakePlaneClient([issue])
    service = FakeService()
    cancelled = handle_stale_autonomy_task_scan(client, service, stale_days=21)
    assert "AUTO-1" in cancelled
    assert ("AUTO-1", "Cancelled") in client.transitions
    # Comment should contain the stale marker
    comments = [c for _, c in client.issue_comments if _ == "AUTO-1"]
    assert any(_STALE_AUTONOMY_CANCEL_MARKER in c for c in comments)


def test_handle_stale_autonomy_task_scan_skips_fresh_task() -> None:
    """Tasks created within stale_days are not cancelled."""
    from datetime import timedelta, timezone as _tz
    fresh_created = (datetime.now(_tz.utc) - timedelta(days=5)).isoformat()
    issue = {
        "id": "AUTO-FRESH",
        "name": "Fix type errors",
        "state": {"name": "Backlog"},
        "labels": [{"name": "source: autonomy"}],
        "created_at": fresh_created,
    }
    client = FakePlaneClient([issue])
    service = FakeService()
    cancelled = handle_stale_autonomy_task_scan(client, service, stale_days=21)
    assert cancelled == []
    assert not client.transitions


def test_handle_stale_autonomy_task_scan_skips_non_autonomy_task() -> None:
    """Tasks without source:autonomy label are not touched even if stale."""
    from datetime import timedelta, timezone as _tz
    stale_created = (datetime.now(_tz.utc) - timedelta(days=30)).isoformat()
    issue = {
        "id": "MANUAL-1",
        "name": "Some manual task",
        "state": {"name": "Backlog"},
        "labels": [{"name": "task-kind: goal"}],
        "created_at": stale_created,
    }
    client = FakePlaneClient([issue])
    service = FakeService()
    cancelled = handle_stale_autonomy_task_scan(client, service, stale_days=21)
    assert cancelled == []


# ---- Human rejection capture ----

def test_handle_feedback_loop_scan_records_cancelled_autonomy_task() -> None:
    """Cancelled autonomy tasks without stale-scan marker are recorded as abandoned."""
    issue = {
        "id": "CANCELLED-AUTO-1",
        "name": "Improve test coverage",
        "state": {"name": "Cancelled"},
        "labels": [{"name": "source: autonomy"}, {"name": "task-kind: goal"}],
        "description": "candidate_dedup_key: test_coverage:myrepo\n",
    }
    client = FakePlaneClient([issue], comments={"CANCELLED-AUTO-1": []})
    service = FakeService()
    feedback_dir = Path(tempfile.mkdtemp())
    # Patch feedback dir
    import control_plane.entrypoints.worker.main as _wm
    original = _wm._FEEDBACK_DIR
    _wm._FEEDBACK_DIR = feedback_dir
    try:
        recorded = handle_feedback_loop_scan(client, service)
    finally:
        _wm._FEEDBACK_DIR = original
    assert "CANCELLED-AUTO-1" in recorded
    feedback_file = feedback_dir / "CANCELLED-AUTO-1.json"
    assert feedback_file.exists()
    import json as _json
    data = _json.loads(feedback_file.read_text())
    assert data["outcome"] == "abandoned"
    assert data["source"] == "human_rejection_capture"


def test_handle_feedback_loop_scan_skips_stale_scan_cancelled_task() -> None:
    """Tasks cancelled by the stale-autonomy-scan are NOT recorded as human rejections."""
    issue = {
        "id": "STALE-CANCELLED-1",
        "name": "Fix something",
        "state": {"name": "Cancelled"},
        "labels": [{"name": "source: autonomy"}],
        "description": "candidate_dedup_key: something:repo\n",
    }
    stale_comment = {"comment_html": f"<p>Cancelled {_STALE_AUTONOMY_CANCEL_MARKER}</p>"}
    client = FakePlaneClient([issue], comments={"STALE-CANCELLED-1": [stale_comment]})
    service = FakeService()
    feedback_dir = Path(tempfile.mkdtemp())
    import control_plane.entrypoints.worker.main as _wm
    original = _wm._FEEDBACK_DIR
    _wm._FEEDBACK_DIR = feedback_dir
    try:
        recorded = handle_feedback_loop_scan(client, service)
    finally:
        _wm._FEEDBACK_DIR = original
    assert "STALE-CANCELLED-1" not in recorded


# ---- Rejection store ----

def test_proposal_rejection_store_records_and_blocks() -> None:
    from control_plane.proposer.rejection_store import ProposalRejectionStore
    store = ProposalRejectionStore(Path(tempfile.mkdtemp()) / "rejections.json")
    assert not store.is_rejected("lint_fix:myrepo")
    store.record_rejection("lint_fix:myrepo", reason="human_cancelled_autonomy_task", task_id="T1")
    assert store.is_rejected("lint_fix:myrepo")
    assert not store.is_rejected("type_fix:myrepo")  # Different key


def test_proposal_rejection_store_deduplicates() -> None:
    from control_plane.proposer.rejection_store import ProposalRejectionStore
    store = ProposalRejectionStore(Path(tempfile.mkdtemp()) / "rejections.json")
    store.record_rejection("k1", reason="r1", task_id="T1")
    store.record_rejection("k1", reason="r2", task_id="T2")  # Should not duplicate
    records = store.all_rejections()
    assert len(records) == 1


# ---- Execution profiles ----

def test_kodo_build_command_with_profile_uses_profile_values() -> None:
    from control_plane.adapters.kodo.adapter import KodoAdapter
    from control_plane.config.settings import KodoSettings
    default_settings = KodoSettings(cycles=3, exchanges=20, effort="standard")
    profile = KodoSettings(cycles=2, exchanges=10, effort="low", binary="kodo", team="full", orchestrator="codex:gpt-5.4", timeout_seconds=3600)
    adapter = KodoAdapter(default_settings)
    cmd = adapter.build_command(Path("/tmp/goal.md"), Path("/tmp/repo"), profile=profile)
    assert "--cycles" in cmd
    cycles_idx = cmd.index("--cycles")
    assert cmd[cycles_idx + 1] == "2"
    effort_idx = cmd.index("--effort")
    assert cmd[effort_idx + 1] == "low"


def test_kodo_build_command_without_profile_uses_default_settings() -> None:
    from control_plane.adapters.kodo.adapter import KodoAdapter
    from control_plane.config.settings import KodoSettings
    settings = KodoSettings(cycles=5, exchanges=30, effort="high")
    adapter = KodoAdapter(settings)
    cmd = adapter.build_command(Path("/tmp/goal.md"), Path("/tmp/repo"))
    cycles_idx = cmd.index("--cycles")
    assert cmd[cycles_idx + 1] == "5"


# ---- Snapshot staleness ----

def test_snapshot_loader_latest_age_hours_none_when_no_snapshots() -> None:
    from control_plane.insights.loader import SnapshotLoader
    loader = SnapshotLoader(root=Path(tempfile.mkdtemp()))
    assert loader.latest_snapshot_age_hours() is None


def test_snapshot_loader_latest_age_hours_returns_float() -> None:
    from control_plane.insights.loader import SnapshotLoader
    root = Path(tempfile.mkdtemp())
    snap_dir = root / "run_001"
    snap_dir.mkdir()
    # Write a minimal valid snapshot file
    import json as _j
    snap_data = {
        "run_id": "run_001",
        "generated_at": "2026-01-01T00:00:00Z",
        "repo": {"name": "myrepo", "path": "/repos/myrepo"},
        "signals": {
            "git": {"recent_commits": [], "active_branches": [], "files_changed_last_30_days": 0},
            "file_hotspots": {"hotspots": []},
            "test_signal": {"status": "unknown", "command": "", "exit_code": 0, "failing_tests": []},
            "dependency_drift": {"status": "unknown", "outdated": [], "vulnerable": []},
            "todo_signal": {"todo_count": 0, "fixme_count": 0, "samples": []},
            "execution_health": {"total_runs": 0, "no_op_count": 0, "validation_failed_count": 0, "recent_runs": []},
            "backlog": {"items": []},
            "lint_signal": {"status": "unavailable", "violation_count": 0, "violations": []},
            "type_signal": {"status": "unavailable", "error_count": 0, "errors": []},
            "ci_history": {"status": "unavailable", "failure_rate": 0.0, "failing_checks": [], "flaky_checks": [], "runs": []},
            "validation_history": {"status": "unavailable", "tasks_analyzed": 0, "tasks_with_repeated_failures": [], "overall_failure_rate": 0.0},
        },
        "collector_errors": [],
    }
    (snap_dir / "repo_state_snapshot.json").write_text(_j.dumps(snap_data))
    loader = SnapshotLoader(root=root)
    age = loader.latest_snapshot_age_hours()
    assert age is not None
    assert age >= 0.0  # Just written, should be very recent


# ---------------------------------------------------------------------------
# Session 5 gap tests
# ---------------------------------------------------------------------------

# S5-1: Plane write retry on transient errors
def test_plane_client_retries_on_503(monkeypatch) -> None:
    """_request should retry on 503 and return the final response."""
    import httpx
    from unittest.mock import MagicMock
    from control_plane.adapters.plane.client import PlaneClient

    call_count = 0
    responses = [httpx.Response(503), httpx.Response(503), httpx.Response(200)]

    inner = MagicMock()
    def fake_request(method, url, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp
    inner.request.side_effect = fake_request

    client = PlaneClient.__new__(PlaneClient)
    client._client = inner
    monkeypatch.setattr("time.sleep", lambda _: None)

    resp = client._request("GET", "/test")
    assert resp.status_code == 200
    assert call_count == 3


def test_plane_client_retries_on_connection_error(monkeypatch) -> None:
    """_request should retry on ConnectError and eventually succeed."""
    import httpx
    from unittest.mock import MagicMock
    from control_plane.adapters.plane.client import PlaneClient

    call_count = 0
    inner = MagicMock()
    def fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.ConnectError("refused")
        return httpx.Response(200)
    inner.request.side_effect = fake_request

    client = PlaneClient.__new__(PlaneClient)
    client._client = inner
    monkeypatch.setattr("time.sleep", lambda _: None)

    resp = client._request("GET", "/test")
    assert resp.status_code == 200
    assert call_count == 2


# S5-2: Kodo process tree cleanup
def test_kodo_adapter_uses_popen_with_new_session(tmp_path, monkeypatch) -> None:
    """run() should call Popen with start_new_session=True."""
    from control_plane.adapters.kodo.adapter import KodoAdapter
    from control_plane.config.settings import KodoSettings

    popen_kwargs: list[dict] = []

    class FakePopen:
        returncode = 0
        def __init__(self, command, **kwargs):
            popen_kwargs.append(kwargs)
        def communicate(self, timeout=None):
            return ("", "")

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    repo = tmp_path / "repo"
    repo.mkdir()
    goal = tmp_path / "goal.md"
    goal.write_text("## Goal\nTest.\n")

    KodoAdapter(KodoSettings()).run(goal, repo)

    assert popen_kwargs, "Popen was not called"
    assert popen_kwargs[0].get("start_new_session") is True


# S5-3: Per-task-kind running TTL in reconcile
def test_reconcile_skips_recently_updated_running_task() -> None:
    """Running tasks updated within the kind TTL should NOT be reconciled."""
    from control_plane.entrypoints.worker.main import reconcile_stale_running_issues
    from unittest.mock import MagicMock
    import tempfile

    recent_ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    issue = {
        "id": "task-r1",
        "state": {"name": "Running"},
        "labels": [{"name": "task-kind: goal"}],
        "updated_at": recent_ts,
        "description": "",
        "name": "Test",
    }
    client = MagicMock()
    client.list_issues.return_value = [issue]

    store = UsageStore(Path(tempfile.mktemp(suffix=".json")))

    reconciled = reconcile_stale_running_issues(client, role="goal", ready_state="Ready for AI", usage_store=store)
    assert reconciled == [], "Recent running task should not be reconciled"


def test_reconcile_acts_on_stale_running_task() -> None:
    """Running tasks updated beyond the kind TTL should be reconciled."""
    from control_plane.entrypoints.worker.main import reconcile_stale_running_issues
    from unittest.mock import MagicMock
    import tempfile

    stale_ts = (datetime.now(UTC) - timedelta(hours=4)).isoformat().replace("+00:00", "Z")
    issue = {
        "id": "task-r2",
        "state": {"name": "Running"},
        "labels": [{"name": "task-kind: goal"}],
        "updated_at": stale_ts,
        "description": "",
        "name": "Test",
    }
    client = MagicMock()
    client.list_issues.return_value = [issue]
    client.list_comments.return_value = []

    store = UsageStore(Path(tempfile.mktemp(suffix=".json")))

    reconciled = reconcile_stale_running_issues(client, role="goal", ready_state="Ready for AI", usage_store=store)
    assert "task-r2" in reconciled


# S5-4: Disk space guardrail
def test_disk_space_check_raises_on_critically_low_space(monkeypatch, tmp_path) -> None:
    """_check_disk_space should raise OSError when free space is critically low."""
    import shutil
    from control_plane.execution.usage_store import _check_disk_space, _DISK_MIN_MB

    DiskUsage = type("DiskUsage", (), {"free": int((_DISK_MIN_MB - 1) * 1024 * 1024)})
    monkeypatch.setattr(shutil, "disk_usage", lambda _: DiskUsage())

    with pytest.raises(OSError, match="disk_space_critical"):
        _check_disk_space(tmp_path / "test.json")


def test_disk_space_check_passes_with_adequate_space(monkeypatch, tmp_path) -> None:
    """_check_disk_space should not raise when plenty of space is available."""
    import shutil
    from control_plane.execution.usage_store import _check_disk_space

    DiskUsage = type("DiskUsage", (), {"free": 10 * 1024 * 1024 * 1024})  # 10 GB
    monkeypatch.setattr(shutil, "disk_usage", lambda _: DiskUsage())

    _check_disk_space(tmp_path / "test.json")  # should not raise


# S5-5: Kodo quota exhaustion detection
def test_kodo_quota_exhausted_detection() -> None:
    """is_quota_exhausted should return True for hard quota signals."""
    from control_plane.adapters.kodo.adapter import KodoAdapter, KodoRunResult

    quota_result = KodoRunResult(1, "", "Error: insufficient_quota for this account", [])
    assert KodoAdapter.is_quota_exhausted(quota_result) is True

    ok_result = KodoRunResult(0, "completed", "", [])
    assert KodoAdapter.is_quota_exhausted(ok_result) is False


def test_usage_store_records_kodo_quota_event(tmp_path) -> None:
    """record_kodo_quota_event should append a kodo_quota_event entry."""
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_kodo_quota_event(task_id="t1", role="goal", now=now)

    data = store.load()
    quota_events = [e for e in data["events"] if e["kind"] == "kodo_quota_event"]
    assert len(quota_events) == 1
    assert quota_events[0]["task_id"] == "t1"


# S5-6: Task urgency scoring
def test_issue_urgency_score_boosts_regression_tasks() -> None:
    """Regression tasks should score higher than routine goal tasks."""
    from control_plane.entrypoints.worker.main import issue_urgency_score

    regression = {"name": "[Regression] CI broke after merge", "labels": []}
    routine = {"name": "Fix lint violations", "labels": []}

    assert issue_urgency_score(regression) > issue_urgency_score(routine)


def test_issue_urgency_score_respects_priority_labels() -> None:
    """High-priority label should yield a higher score than unset priority."""
    from control_plane.entrypoints.worker.main import issue_urgency_score

    high_pri = {"name": "Fix something", "labels": [{"name": "priority: high"}]}
    no_pri = {"name": "Fix something", "labels": []}

    assert issue_urgency_score(high_pri) > issue_urgency_score(no_pri)


# S5-7: Board saturation backpressure
def test_handle_propose_cycle_suppressed_when_board_saturated(tmp_path) -> None:
    """Proposal creation should be suppressed when autonomy queue is full."""
    from control_plane.entrypoints.worker.main import (
        handle_propose_cycle,
        MAX_QUEUED_AUTONOMY_TASKS,
    )
    from unittest.mock import MagicMock

    autonomy_issues = [
        {
            "id": f"task-{i}",
            "state": {"name": "Backlog"},
            "labels": [
                {"name": "task-kind: goal"},
                {"name": "source: autonomy"},
            ],
            "name": f"Autonomy task {i}",
            "description": "",
        }
        for i in range(MAX_QUEUED_AUTONOMY_TASKS)
    ]
    client = MagicMock()
    client.list_issues.return_value = autonomy_issues

    service = MagicMock()
    service.settings.execution_controls.return_value = MagicMock(min_remaining_exec_for_proposals=1)
    service.usage_store.remaining_exec_capacity.return_value = 10
    service.usage_store.is_proposal_satiated.return_value = False
    service.settings.scheduled_tasks = None

    result = handle_propose_cycle(client, service, now=datetime.now(UTC))
    assert result.decision == "board_saturated"


# S5-8: Scope violation usage store recording
def test_record_scope_violation_stored_in_events(tmp_path) -> None:
    """record_scope_violation should persist a scope_violation event."""
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_scope_violation(
        task_id="t1",
        repo_key="myrepo",
        violated_files=["src/outside.py"],
        now=now,
    )

    data = store.load()
    events = [e for e in data["events"] if e["kind"] == "scope_violation"]
    assert len(events) == 1
    assert events[0]["repo_key"] == "myrepo"
    assert "src/outside.py" in events[0]["violated_files"]


# S5-9: Improve → propose systemic feedback
def test_systemic_fix_task_created_on_escalation(tmp_path) -> None:
    """When should_escalate fires, a systemic-fix goal task should be created."""
    from control_plane.entrypoints.worker.main import handle_blocked_triage
    from unittest.mock import MagicMock

    issue = {
        "id": "task-blk",
        "name": "Failing task",
        "state": {"name": "Blocked"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: myrepo"}],
        "description": "## Execution\nrepo: myrepo\nmode: goal\n\n## Goal\nDo stuff.",
    }

    client = MagicMock()
    client.fetch_issue.return_value = issue
    client.list_comments.return_value = []
    client.list_issues.return_value = []

    esc_settings = MagicMock()
    esc_settings.webhook_url = "http://hook.test"
    esc_settings.block_threshold = 1
    esc_settings.cooldown_seconds = 0

    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    # Pre-load a triage event so should_escalate fires immediately
    for _ in range(2):
        store.record_blocked_triage(task_id="task-blk", classification="validation_failure", now=now)

    service = MagicMock()
    service.usage_store = store
    service.settings.escalation = esc_settings
    service.settings.repos = {"myrepo": MagicMock(default_branch="main")}

    handle_blocked_triage(client, service, "task-blk")

    create_calls = client.create_issue.call_args_list
    systemic_calls = [
        c for c in create_calls
        if "Systemic" in (c.kwargs.get("name") or (c.args[0] if c.args else ""))
    ]
    assert len(systemic_calls) >= 1, "Systemic fix task was not created"


# S5-10: Kodo quality erosion detection
def test_count_quality_suppressions_counts_noqa_additions() -> None:
    """_count_quality_suppressions should count +noqa lines in diff."""
    from control_plane.application.service import ExecutionService

    diff = """\
diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,5 @@
 existing = 1
+bad_line = True  # noqa: E501
+another = "x"  # noqa
-removed_line  # noqa
 other = 2
"""
    counts = ExecutionService._count_quality_suppressions(diff)
    assert counts["noqa"] == 2   # only '+' lines counted
    assert counts["type_ignore"] == 0


def test_count_quality_suppressions_counts_type_ignore() -> None:
    """_count_quality_suppressions should count +type: ignore lines."""
    from control_plane.application.service import ExecutionService

    diff = """\
+result = bad_fn()  # type: ignore
+value: int = other()  # type:ignore[assignment]
"""
    counts = ExecutionService._count_quality_suppressions(diff)
    assert counts["type_ignore"] == 2


def test_quality_warning_event_recorded_in_usage_store(tmp_path) -> None:
    """record_quality_warning should persist a kodo_quality_warning event."""
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_quality_warning(
        task_id="t1",
        repo_key="myrepo",
        suppression_counts={"noqa": 4, "type_ignore": 0, "bare_pass": 0},
        now=now,
    )

    data = store.load()
    events = [e for e in data["events"] if e["kind"] == "kodo_quality_warning"]
    assert len(events) == 1
    assert events[0]["suppression_counts"]["noqa"] == 4


# ---------------------------------------------------------------------------
# S6 tests
# ---------------------------------------------------------------------------

# S6-1: Maintenance window helper
def test_in_maintenance_window_returns_true_when_in_window() -> None:
    from control_plane.entrypoints.worker.main import _in_maintenance_window

    class FakeWindow:
        start_hour = 2
        end_hour = 4
        days: list[int] = []

    class FakeSettings:
        maintenance_windows = [FakeWindow()]

    now = datetime(2026, 1, 5, 3, 0, 0, tzinfo=UTC)  # 03:00 UTC, Monday
    assert _in_maintenance_window(FakeSettings(), now) is True


def test_in_maintenance_window_returns_false_outside_window() -> None:
    from control_plane.entrypoints.worker.main import _in_maintenance_window

    class FakeWindow:
        start_hour = 2
        end_hour = 4
        days: list[int] = []

    class FakeSettings:
        maintenance_windows = [FakeWindow()]

    now = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)  # 10:00 UTC
    assert _in_maintenance_window(FakeSettings(), now) is False


def test_in_maintenance_window_wraps_midnight() -> None:
    from control_plane.entrypoints.worker.main import _in_maintenance_window

    class FakeWindow:
        start_hour = 22
        end_hour = 2   # wraps: 22–24, 0–2
        days: list[int] = []

    class FakeSettings:
        maintenance_windows = [FakeWindow()]

    in_window = datetime(2026, 1, 5, 23, 30, 0, tzinfo=UTC)
    out_of_window = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
    assert _in_maintenance_window(FakeSettings(), in_window) is True
    assert _in_maintenance_window(FakeSettings(), out_of_window) is False


def test_in_maintenance_window_day_filter_skips_wrong_day() -> None:
    from control_plane.entrypoints.worker.main import _in_maintenance_window

    class FakeWindow:
        start_hour = 2
        end_hour = 4
        days = [0, 1, 2, 3, 4]  # weekdays only

    class FakeSettings:
        maintenance_windows = [FakeWindow()]

    # Saturday (weekday 5) should not be in window
    saturday = datetime(2026, 1, 10, 3, 0, 0, tzinfo=UTC)
    assert _in_maintenance_window(FakeSettings(), saturday) is False


# S6-2: Per-repo budget check
def test_budget_decision_for_repo_blocks_when_limit_reached(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)

    # Simulate 3 executions for this repo in the last 24h
    for i in range(3):
        store.record_execution(
            role="goal",
            task_id=f"t{i}",
            signature=f"sig{i}",
            repo_key="myrepo",
            now=now,
        )

    decision = store.budget_decision_for_repo("myrepo", max_daily=3, now=now)
    assert decision.allowed is False
    assert decision.reason == "repo_budget_exceeded"
    assert decision.current == 3


def test_budget_decision_for_repo_allows_when_under_limit(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)

    for i in range(2):
        store.record_execution(
            role="goal",
            task_id=f"t{i}",
            signature=f"sig{i}",
            repo_key="myrepo",
            now=now,
        )

    decision = store.budget_decision_for_repo("myrepo", max_daily=3, now=now)
    assert decision.allowed is True


# S6-4: Failure rate degradation check
def test_check_failure_rate_degradation_returns_rate_when_degraded(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)

    # 7 failures, 3 successes → 30% success rate (below default 60% warn threshold)
    for i in range(7):
        store.record_execution_outcome(task_id=f"fail{i}", role="goal", succeeded=False, now=now)
    for i in range(3):
        store.record_execution_outcome(task_id=f"ok{i}", role="goal", succeeded=True, now=now)

    rate = store.check_failure_rate_degradation(now=now)
    assert rate is not None
    assert rate == pytest.approx(0.3)


def test_check_failure_rate_degradation_returns_none_when_healthy(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)

    for i in range(8):
        store.record_execution_outcome(task_id=f"ok{i}", role="goal", succeeded=True, now=now)
    for i in range(2):
        store.record_execution_outcome(task_id=f"fail{i}", role="goal", succeeded=False, now=now)

    rate = store.check_failure_rate_degradation(now=now)
    assert rate is None  # 80% success rate is healthy


def test_check_failure_rate_degradation_returns_none_below_minimum_samples(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)

    for i in range(3):
        store.record_execution_outcome(task_id=f"fail{i}", role="goal", succeeded=False, now=now)

    rate = store.check_failure_rate_degradation(now=now)
    assert rate is None  # < 5 samples


# S6-5: Execution duration baseline
def test_record_execution_duration_persists_event(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_execution_duration(task_id="t1", role="goal", duration_seconds=120.5, now=now)

    data = store.load()
    events = [e for e in data["events"] if e["kind"] == "execution_duration"]
    assert len(events) == 1
    assert events[0]["duration_seconds"] == pytest.approx(120.5)
    assert events[0]["role"] == "goal"


def test_median_execution_duration_returns_none_below_minimum(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    for i in range(2):
        store.record_execution_duration(task_id=f"t{i}", role="goal", duration_seconds=float(100 + i * 10), now=now)
    assert store.median_execution_duration("goal") is None


def test_median_execution_duration_returns_correct_value(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    durations = [60.0, 90.0, 120.0, 150.0, 180.0]
    for i, d in enumerate(durations):
        store.record_execution_duration(task_id=f"t{i}", role="goal", duration_seconds=d, now=now)
    median = store.median_execution_duration("goal")
    assert median == pytest.approx(120.0)


# S6-8: Kodo version stored in execution outcomes
def test_record_execution_outcome_stores_kodo_version(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_execution_outcome(
        task_id="t1",
        role="goal",
        succeeded=True,
        now=now,
        kodo_version="kodo 1.2.3",
    )

    data = store.load()
    events = [e for e in data["events"] if e["kind"] == "execution_outcome"]
    assert len(events) == 1
    assert events[0]["kodo_version"] == "kodo 1.2.3"


def test_circuit_breaker_skips_on_mixed_kodo_versions(tmp_path) -> None:
    """Circuit breaker should NOT fire when failures span multiple kodo versions."""
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)

    # 5 failures but across 2 different kodo versions
    for i in range(3):
        store.record_execution_outcome(
            task_id=f"t{i}", role="goal", succeeded=False, now=now, kodo_version="kodo 1.0.0"
        )
    for i in range(3, 6):
        store.record_execution_outcome(
            task_id=f"t{i}", role="goal", succeeded=False, now=now, kodo_version="kodo 2.0.0"
        )

    decision = store.budget_decision(now=now)
    # Mixed versions → circuit breaker should NOT block
    assert decision.allowed is True


# S6-9: Audit export
def test_audit_export_returns_execution_outcomes(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_execution_outcome(task_id="t1", role="goal", succeeded=True, now=now)
    store.record_execution_outcome(task_id="t2", role="goal", succeeded=False, now=now)

    events = store.audit_export(window_days=1, now=now)
    assert len(events) == 2
    # audit_export maps execution_outcome → kind "execution"
    assert all(e["kind"] == "execution" for e in events)
    outcomes = {e["task_id"]: e["outcome"] for e in events}
    assert outcomes["t1"] == "succeeded"
    assert outcomes["t2"] == "failed"


def test_audit_export_excludes_old_events(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    old = now - timedelta(days=10)
    store.record_execution_outcome(task_id="t1", role="goal", succeeded=True, now=old)
    store.record_execution_outcome(task_id="t2", role="goal", succeeded=True, now=now)

    events = store.audit_export(window_days=1, now=now)
    assert len(events) == 1
    assert events[0]["task_id"] == "t2"


# S6-10: Board health check
def test_board_health_check_detects_stuck_running(tmp_path) -> None:
    from control_plane.entrypoints.worker.main import board_health_check

    issues = [
        {"id": str(i), "state": {"name": "Running"}, "labels": []}
        for i in range(4)
    ]

    class FakeSettings:
        repos: dict = {}

    class FakeService:
        settings = FakeSettings()

    anomalies = board_health_check(issues, FakeService())
    stuck = [a for a in anomalies if a["kind"] == "stuck_running"]
    assert len(stuck) == 1
    assert stuck[0]["count"] == 4


def test_board_health_check_detects_clustered_blocked_reasons(tmp_path) -> None:
    from control_plane.entrypoints.worker.main import board_health_check

    issues = [
        {"id": str(i), "state": {"name": "Blocked"}, "labels": [{"name": "blocked: scope_violation"}]}
        for i in range(6)
    ]

    class FakeSettings:
        repos: dict = {}

    class FakeService:
        settings = FakeSettings()

    anomalies = board_health_check(issues, FakeService())
    clustered = [a for a in anomalies if a["kind"] == "clustered_blocked_reason"]
    assert len(clustered) == 1
    assert clustered[0]["count"] == 6


def test_board_health_check_detects_quiet_repo(tmp_path) -> None:
    from control_plane.entrypoints.worker.main import board_health_check

    issues: list[dict] = []  # no active tasks

    class FakeRepos:
        def __iter__(self):
            return iter(["myrepo"])

    class FakeSettings:
        repos = FakeRepos()

    class FakeService:
        settings = FakeSettings()

    anomalies = board_health_check(issues, FakeService())
    quiet = [a for a in anomalies if a["kind"] == "quiet_repo_lane"]
    assert len(quiet) == 1
    assert quiet[0]["repo_key"] == "myrepo"


def test_board_health_check_returns_empty_for_healthy_board() -> None:
    from control_plane.entrypoints.worker.main import board_health_check

    issues = [
        {"id": "1", "state": {"name": "Ready for AI"}, "labels": [{"name": "repo: myrepo"}]},
        {"id": "2", "state": {"name": "Done"}, "labels": [{"name": "repo: myrepo"}]},
    ]

    class FakeRepos:
        def __iter__(self):
            return iter(["myrepo"])

    class FakeSettings:
        repos = FakeRepos()

    class FakeService:
        settings = FakeSettings()

    anomalies = board_health_check(issues, FakeService())
    # Should have no stuck_running, no clustered, and no quiet_repo (myrepo has 1 active task)
    assert all(a["kind"] != "stuck_running" for a in anomalies)
    assert all(a["kind"] != "clustered_blocked_reason" for a in anomalies)
    assert all(a["kind"] != "quiet_repo_lane" for a in anomalies)
