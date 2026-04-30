# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for S7 autonomy gaps.

Coverage:
  S7-1: Process supervisor (spawn, crash-restart, heartbeat-stale restart)
  S7-4: Self-healing on repeated blocked tasks (consecutive_blocks_for_task)
  S7-7: Human escalation wiring (circuit-breaker escalation, quiet-cycle escalation)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# S7-1: Process supervisor
# ---------------------------------------------------------------------------

from operations_center.entrypoints.supervisor.main import (
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
# S7-4: Self-healing — consecutive_blocks_for_task
# ---------------------------------------------------------------------------

from operations_center.execution.usage_store import UsageStore  # noqa: E402


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
# S7-7: Circuit-breaker escalation + quiet-cycle escalation
# ---------------------------------------------------------------------------

from operations_center.entrypoints.autonomy_cycle.main import _write_quiet_diagnosis  # noqa: E402


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

    with patch("operations_center.adapters.escalation.post_escalation", side_effect=fake_post):
        with patch("operations_center.execution.usage_store.UsageStore.should_escalate", return_value=(True, [])):
            with patch("operations_center.execution.usage_store.UsageStore.record_escalation"):
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

    with patch("operations_center.adapters.escalation.post_escalation", side_effect=fake_post):
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

    with patch("operations_center.adapters.escalation.post_escalation", side_effect=fake_post):
        # No webhook
        _write_quiet_diagnosis(report_dir, quiet_window=5, escalation_webhook="")

    assert escalation_calls == []
