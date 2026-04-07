"""Tests for S7 autonomy gaps.

Coverage:
  S7-1: Process supervisor (spawn, crash-restart, heartbeat-stale restart)
  S7-2: Credential rotation detection (expiry header warning / escalation)
  S7-3: Transcript failure classification (timeout, model_error, oom, tool_failure)
  S7-4: Self-healing on repeated blocked tasks (consecutive_blocks_for_task)
  S7-5: Dependency update loop (handle_dependency_update_scan)
  S7-6: Cross-repo impact analysis (_check_cross_repo_impact)
  S7-7: Human escalation wiring (circuit-breaker escalation, quiet-cycle escalation)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# S7-1: Process supervisor
# ---------------------------------------------------------------------------

from control_plane.entrypoints.supervisor.main import (
    ManagedProcess,
    _heartbeat_age_seconds,
    _is_alive,
    _maybe_restart,
    _spawn,
    _terminate,
)


def test_supervisor_spawn_starts_process(tmp_path: Path) -> None:
    mp = ManagedProcess(role="test", command=["sleep", "60"])
    _spawn(mp)
    assert mp.proc is not None
    assert mp.proc.poll() is None
    _terminate(mp)


def test_supervisor_terminate_kills_process(tmp_path: Path) -> None:
    mp = ManagedProcess(role="test", command=["sleep", "60"])
    _spawn(mp)
    pid = mp.proc.pid
    _terminate(mp)
    # Process should be gone
    assert mp.proc is None
    # pid should no longer exist
    import os
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_supervisor_heartbeat_age_returns_none_when_missing(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    age = _heartbeat_age_seconds(tmp_path, "nonexistent", now)
    assert age is None


def test_supervisor_heartbeat_age_returns_seconds(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    hb_file = tmp_path / "heartbeat_goal.json"
    ts = (now - timedelta(seconds=90)).isoformat()
    hb_file.write_text(json.dumps({"role": "goal", "ts": ts}))
    age = _heartbeat_age_seconds(tmp_path, "goal", now)
    assert age is not None
    assert 85 <= age <= 100


def test_supervisor_maybe_restart_respects_max(tmp_path: Path) -> None:
    mp = ManagedProcess(role="test", command=["sleep", "0.01"], restart_max=1, restart_backoff_seconds=0)
    _spawn(mp)
    mp.proc.wait()  # let it exit naturally
    assert _maybe_restart(mp, reason="test") is True
    assert mp.restart_count == 1
    # second restart should be denied
    _terminate(mp)
    mp.proc = None
    assert _maybe_restart(mp, reason="test") is False
    assert mp.restart_count == 1


def test_supervisor_maybe_restart_after_exit(tmp_path: Path) -> None:
    """_maybe_restart restarts a process that has already exited."""
    mp = ManagedProcess(
        role="quick",
        command=["python3", "-c", "pass"],
        restart_backoff_seconds=0,
    )
    _spawn(mp)
    mp.proc.wait()  # wait until it's definitely dead
    assert not _is_alive(mp)
    assert _maybe_restart(mp, reason="exited") is True
    assert mp.restart_count == 1
    _terminate(mp)


# ---------------------------------------------------------------------------
# S7-2: Credential rotation detection
# ---------------------------------------------------------------------------

from control_plane.entrypoints.worker.main import validate_credentials  # noqa: E402
from control_plane.execution.usage_store import UsageStore  # noqa: E402


def test_credential_expiry_warning_logs_when_close(tmp_path: Path, caplog) -> None:
    """validate_credentials logs a warning when token expires within warn_days."""
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    expiry = now + timedelta(days=3)
    expiry_str = expiry.strftime("%Y-%m-%dT%H:%M:%SZ")

    settings = SimpleNamespace(
        git_token=lambda: "fake_token",
        plane_token=lambda: "fake_plane",
        plane=SimpleNamespace(
            base_url="http://plane.local",
            workspace_slug="ws",
        ),
        escalation=SimpleNamespace(
            credential_expiry_warn_days=7,
            webhook_url="",
        ),
    )

    fake_gh_response = MagicMock()
    fake_gh_response.status_code = 200
    fake_gh_response.headers = {"x-token-expiration": expiry_str}

    fake_plane_response = MagicMock()
    fake_plane_response.status_code = 200
    fake_plane_response.headers = {}

    import httpx
    with patch.object(httpx, "get", side_effect=[fake_gh_response, fake_plane_response]):
        import logging
        with caplog.at_level(logging.WARNING, logger="control_plane.entrypoints.worker.main"):
            result = validate_credentials(settings, usage_store=store, now=now)

    assert result is True
    assert any("credential_expiry_soon" in r.message for r in caplog.records)


def test_credential_expiry_escalates_when_one_day_left(tmp_path: Path) -> None:
    """validate_credentials records escalation when token expires within 1 day."""
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    expiry = now + timedelta(hours=12)
    expiry_str = expiry.strftime("%Y-%m-%dT%H:%M:%SZ")

    settings = SimpleNamespace(
        git_token=lambda: "fake_token",
        plane_token=lambda: "fake_plane",
        plane=SimpleNamespace(
            base_url="http://plane.local",
            workspace_slug="ws",
        ),
        escalation=SimpleNamespace(
            credential_expiry_warn_days=7,
            webhook_url="",
        ),
    )

    fake_gh_response = MagicMock()
    fake_gh_response.status_code = 200
    fake_gh_response.headers = {"x-token-expiration": expiry_str}

    fake_plane_response = MagicMock()
    fake_plane_response.status_code = 200
    fake_plane_response.headers = {}

    import httpx
    with patch.object(httpx, "get", side_effect=[fake_gh_response, fake_plane_response]):
        validate_credentials(settings, usage_store=store, now=now)

    data = store.load()
    events = data.get("events", [])
    esc_events = [e for e in events if e.get("kind") == "escalation_sent" and "expiring" in e.get("classification", "")]
    assert len(esc_events) == 1


# ---------------------------------------------------------------------------
# S7-3: Transcript failure classification
# ---------------------------------------------------------------------------

from control_plane.entrypoints.worker.main import classify_execution_result  # noqa: E402
from control_plane.domain.models import ExecutionResult  # noqa: E402


def _make_result(**kwargs) -> ExecutionResult:
    defaults = dict(
        run_id="r1",
        success=False,
        validation_passed=False,
        summary="test",
    )
    defaults.update(kwargs)
    return ExecutionResult(**defaults)


def test_classify_timeout() -> None:
    r = _make_result(execution_stderr_excerpt="Operation timed out after 3600s")
    assert classify_execution_result(r) == "timeout"


def test_classify_timeout_via_timed_out() -> None:
    r = _make_result(execution_stderr_excerpt="Process timed out")
    assert classify_execution_result(r) == "timeout"


def test_classify_model_error() -> None:
    r = _make_result(execution_stderr_excerpt="Anthropic API Error: internal server error")
    assert classify_execution_result(r) == "model_error"


def test_classify_model_error_overloaded() -> None:
    r = _make_result(execution_stderr_excerpt="overloaded — please retry")
    assert classify_execution_result(r) == "model_error"


def test_classify_oom() -> None:
    r = _make_result(execution_stderr_excerpt="Killed: out of memory")
    assert classify_execution_result(r) == "oom"


def test_classify_oom_cannot_allocate() -> None:
    r = _make_result(execution_stderr_excerpt="cannot allocate memory")
    assert classify_execution_result(r) == "oom"


def test_classify_tool_failure() -> None:
    r = _make_result(execution_stderr_excerpt="tool_error: bash tool failed", validation_passed=True)
    assert classify_execution_result(r) == "tool_failure"


def test_classify_context_limit_still_works() -> None:
    r = _make_result(execution_stderr_excerpt="context window exceeded")
    assert classify_execution_result(r) == "context_limit"


def test_classify_timeout_takes_priority_over_infra_tooling() -> None:
    # "timeout" used to fall into infra_tooling; it now has its own category
    r = _make_result(execution_stderr_excerpt="error: authentication failed, timeout")
    # timeout should match first
    assert classify_execution_result(r) == "timeout"


# ---------------------------------------------------------------------------
# S7-4: Self-healing — consecutive_blocks_for_task
# ---------------------------------------------------------------------------


def test_consecutive_blocks_zero_when_no_events(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    assert store.consecutive_blocks_for_task("TASK-1", now=now) == 0


def test_consecutive_blocks_counts_blocked_triage(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_blocked_triage(task_id="TASK-1", classification="unknown", now=now)
    store.record_blocked_triage(task_id="TASK-1", classification="context_limit", now=now)
    assert store.consecutive_blocks_for_task("TASK-1", now=now) == 2


def test_consecutive_blocks_resets_after_success(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_blocked_triage(task_id="TASK-1", classification="unknown", now=now)
    store.record_execution_outcome(task_id="TASK-1", role="goal", succeeded=True, now=now)
    store.record_blocked_triage(task_id="TASK-1", classification="timeout", now=now)
    # Only 1 block after the success
    assert store.consecutive_blocks_for_task("TASK-1", now=now) == 1


def test_consecutive_blocks_ignores_other_tasks(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.json")
    now = datetime.now(UTC)
    store.record_blocked_triage(task_id="TASK-2", classification="unknown", now=now)
    store.record_blocked_triage(task_id="TASK-2", classification="unknown", now=now)
    assert store.consecutive_blocks_for_task("TASK-1", now=now) == 0


# ---------------------------------------------------------------------------
# S7-5: Dependency update loop
# ---------------------------------------------------------------------------

from control_plane.entrypoints.worker.main import handle_dependency_update_scan  # noqa: E402


class _FakePlaneDep:
    """Minimal PlaneClient stub for dependency update tests."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self._issue_counter = 0

    def list_issues(self):
        return []

    def create_issue(self, *, name, description, state, label_names):
        self._issue_counter += 1
        issue = {"id": f"DEP-{self._issue_counter}", "name": name}
        self.created.append(issue)
        return issue


class _FakeRepoSettings:
    def __init__(self, local_path: str | None = None) -> None:
        self.local_path = local_path
        self.default_branch = "main"
        self.impact_report_paths: list[str] = []


class _FakeSettings:
    def __init__(self, repos: dict) -> None:
        self.repos = repos


class _FakeService:
    def __init__(self, settings) -> None:
        self.settings = settings


def test_dependency_update_scan_creates_task_for_major_bump(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    pip_output = json.dumps([
        {"name": "requests", "version": "2.31.0", "latest_version": "3.0.0", "latest_filetype": "wheel"},
    ])
    client = _FakePlaneDep()
    settings = _FakeSettings(repos={"repo_a": _FakeRepoSettings(local_path=str(repo_path))})
    service = _FakeService(settings)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=pip_output, stderr="")
        created = handle_dependency_update_scan(client, service)

    assert len(created) == 1
    assert "requests" in client.created[0]["name"]
    assert "3.0.0" in client.created[0]["name"]


def test_dependency_update_scan_skips_minor_bumps(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    pip_output = json.dumps([
        {"name": "requests", "version": "2.31.0", "latest_version": "2.32.0", "latest_filetype": "wheel"},
    ])
    client = _FakePlaneDep()
    settings = _FakeSettings(repos={"repo_a": _FakeRepoSettings(local_path=str(repo_path))})
    service = _FakeService(settings)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=pip_output, stderr="")
        created = handle_dependency_update_scan(client, service)

    assert created == []


def test_dependency_update_scan_skips_repos_without_local_path() -> None:
    client = _FakePlaneDep()
    settings = _FakeSettings(repos={"repo_a": _FakeRepoSettings(local_path=None)})
    service = _FakeService(settings)

    created = handle_dependency_update_scan(client, service)
    assert created == []


def test_dependency_update_scan_deduplicates_existing_tasks(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    pip_output = json.dumps([
        {"name": "requests", "version": "2.31.0", "latest_version": "3.0.0", "latest_filetype": "wheel"},
    ])

    class _DupClient(_FakePlaneDep):
        def list_issues(self):
            return [{"name": "Update requests from 2.31.0 to 3.0.0 in repo_a", "state": {"name": "Backlog"}, "labels": []}]

    client = _DupClient()
    settings = _FakeSettings(repos={"repo_a": _FakeRepoSettings(local_path=str(repo_path))})
    service = _FakeService(settings)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=pip_output, stderr="")
        created = handle_dependency_update_scan(client, service)

    assert created == []


# ---------------------------------------------------------------------------
# S7-6: Cross-repo impact analysis
# ---------------------------------------------------------------------------

from control_plane.entrypoints.worker.main import _check_cross_repo_impact  # noqa: E402


def test_cross_repo_no_impact_when_no_shared_paths() -> None:
    settings = _FakeSettings(repos={
        "repo_a": _FakeRepoSettings(),
        "repo_b": _FakeRepoSettings(),
    })
    service = _FakeService(settings)
    warnings = _check_cross_repo_impact(["src/foo.py", "tests/test_foo.py"], service)
    assert warnings == []


def test_cross_repo_detects_shared_path_match() -> None:
    repo_a = _FakeRepoSettings()
    repo_a.impact_report_paths = ["src/api/"]

    settings = _FakeSettings(repos={
        "repo_a": repo_a,
        "repo_b": _FakeRepoSettings(),
    })
    service = _FakeService(settings)
    warnings = _check_cross_repo_impact(["src/api/client.py", "tests/test_api.py"], service)
    assert len(warnings) == 1
    assert "repo_a" in warnings[0]
    assert "src/api/client.py" in warnings[0]


def test_cross_repo_no_match_when_prefix_differs() -> None:
    repo_a = _FakeRepoSettings()
    repo_a.impact_report_paths = ["src/api/"]

    settings = _FakeSettings(repos={"repo_a": repo_a})
    service = _FakeService(settings)
    warnings = _check_cross_repo_impact(["src/internal/helpers.py"], service)
    assert warnings == []


def test_cross_repo_multiple_repos_flagged() -> None:
    repo_a = _FakeRepoSettings()
    repo_a.impact_report_paths = ["proto/"]
    repo_b = _FakeRepoSettings()
    repo_b.impact_report_paths = ["proto/"]

    settings = _FakeSettings(repos={"repo_a": repo_a, "repo_b": repo_b})
    service = _FakeService(settings)
    warnings = _check_cross_repo_impact(["proto/service.proto"], service)
    repos_warned = {w.split(" ")[0].split("=")[1] for w in warnings}
    assert "repo_a" in repos_warned
    assert "repo_b" in repos_warned


# ---------------------------------------------------------------------------
# S7-7: Circuit-breaker escalation + quiet-cycle escalation
# ---------------------------------------------------------------------------

from control_plane.entrypoints.autonomy_cycle.main import _write_quiet_diagnosis  # noqa: E402


def test_quiet_diagnosis_fires_escalation_when_quiet(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    # Write 5 cycle reports all with 0 candidates
    for i in range(5):
        (report_dir / f"cycle_2024010{i+1}T000000Z.json").write_text(json.dumps({
            "stages": {"decide": {"candidates_emitted": 0, "suppression_reasons": {"cooldown_active": 3}, "emitted_families": []}},
        }))

    escalation_calls: list[dict] = []

    def fake_post(url, *, classification, count, task_ids, now):
        escalation_calls.append({"url": url, "classification": classification})

    with patch("control_plane.adapters.escalation.post_escalation", side_effect=fake_post):
        with patch("control_plane.execution.usage_store.UsageStore.should_escalate", return_value=(True, [])):
            with patch("control_plane.execution.usage_store.UsageStore.record_escalation"):
                _write_quiet_diagnosis(
                    report_dir,
                    quiet_window=5,
                    escalation_webhook="http://hooks.test/alert",
                )

    assert len(escalation_calls) == 1
    assert escalation_calls[0]["classification"] == "proposer_quiet"


def test_quiet_diagnosis_no_escalation_when_below_window(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    # Only 3 reports — not enough for a quiet window of 5
    for i in range(3):
        (report_dir / f"cycle_2024010{i+1}T000000Z.json").write_text(json.dumps({
            "stages": {"decide": {"candidates_emitted": 0, "suppression_reasons": {}, "emitted_families": []}},
        }))

    escalation_calls: list[dict] = []

    def fake_post(url, **kwargs):
        escalation_calls.append({"url": url})

    with patch("control_plane.adapters.escalation.post_escalation", side_effect=fake_post):
        _write_quiet_diagnosis(
            report_dir,
            quiet_window=5,
            escalation_webhook="http://hooks.test/alert",
        )

    assert escalation_calls == []


def test_quiet_diagnosis_no_escalation_when_no_webhook(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    for i in range(5):
        (report_dir / f"cycle_2024010{i+1}T000000Z.json").write_text(json.dumps({
            "stages": {"decide": {"candidates_emitted": 0, "suppression_reasons": {}, "emitted_families": []}},
        }))

    escalation_calls: list[dict] = []

    def fake_post(url, **kwargs):
        escalation_calls.append({"url": url})

    with patch("control_plane.adapters.escalation.post_escalation", side_effect=fake_post):
        # No webhook
        _write_quiet_diagnosis(report_dir, quiet_window=5, escalation_webhook="")

    assert escalation_calls == []
