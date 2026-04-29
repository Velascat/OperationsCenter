# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.execution.usage_store import UsageStore


def test_usage_store_enforces_hourly_and_daily_budget(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_HOUR", "2")
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_DAY", "3")
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)

    store.record_execution(role="goal", task_id="A", signature="sig-a", now=now - timedelta(minutes=30))
    store.record_execution(role="goal", task_id="B", signature="sig-b", now=now - timedelta(minutes=10))

    decision = store.budget_decision(now=now)
    assert decision.allowed is False
    assert decision.window == "hourly"
    assert decision.current == 2

    store.record_execution(role="goal", task_id="C", signature="sig-c", now=now - timedelta(hours=2))
    decision = store.budget_decision(now=now + timedelta(hours=2))
    assert decision.allowed is False
    assert decision.window == "daily"


def test_usage_store_tracks_retry_and_noop_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", "2")
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)

    assert store.noop_decision(role="goal", task_id="T1", signature="sig-1").should_skip is False
    store.record_execution(role="goal", task_id="T1", signature="sig-1", now=now)
    assert store.noop_decision(role="goal", task_id="T1", signature="sig-1").should_skip is True

    assert store.retry_decision(task_id="T1", now=now + timedelta(minutes=1)).allowed is True
    store.record_execution(role="goal", task_id="T1", signature="sig-2", now=now + timedelta(minutes=1))
    # Cap exceeded and last attempt was recent (<1h) → still blocked
    retry = store.retry_decision(task_id="T1", now=now + timedelta(minutes=2))
    assert retry.allowed is False
    assert retry.attempts == 2
    # After >1h gap (human unblocked it) → auto-reset, allowed again
    retry_after_gap = store.retry_decision(task_id="T1", now=now + timedelta(hours=2))
    assert retry_after_gap.allowed is True


def test_retry_cap_reset_only_after_one_hour_gap(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", "1")
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)

    store.record_execution(role="goal", task_id="T1", signature="sig-1", now=now)

    # At +30min: cap hit and recent → blocked
    retry = store.retry_decision(task_id="T1", now=now + timedelta(minutes=30))
    assert retry.allowed is False

    # At +59min: still within 1h window → blocked
    retry = store.retry_decision(task_id="T1", now=now + timedelta(minutes=59))
    assert retry.allowed is False

    # At +61min: >1h gap triggers reset → allowed
    retry = store.retry_decision(task_id="T1", now=now + timedelta(minutes=61))
    assert retry.allowed is True
    assert retry.attempts == 0


def test_retry_cap_reset_clears_signatures(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", "1")
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)

    store.record_execution(role="goal", task_id="T1", signature="sig-a", now=now)

    # Cap is hit
    retry = store.retry_decision(task_id="T1", now=now + timedelta(minutes=10))
    assert retry.allowed is False

    # Wait >1h to trigger reset
    retry = store.retry_decision(task_id="T1", now=now + timedelta(hours=2))
    assert retry.allowed is True

    # Signature was cleared by reset → noop_decision should not skip
    noop = store.noop_decision(role="goal", task_id="T1", signature="sig-a")
    assert noop.should_skip is False


def test_retry_cap_recent_block_preserves_attempts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_RETRIES_PER_TASK", "2")
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)

    store.record_execution(role="goal", task_id="T1", signature="sig-1", now=now)
    store.record_execution(role="goal", task_id="T1", signature="sig-2", now=now + timedelta(minutes=1))

    # Blocked at +10min
    retry = store.retry_decision(task_id="T1", now=now + timedelta(minutes=10))
    assert retry.allowed is False
    assert retry.attempts == 2

    # Blocked again at +20min — attempts unchanged (no mutation from repeated blocked checks)
    retry = store.retry_decision(task_id="T1", now=now + timedelta(minutes=20))
    assert retry.allowed is False
    assert retry.attempts == 2
