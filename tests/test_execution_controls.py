from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from control_plane.execution.usage_store import UsageStore


def test_usage_store_enforces_hourly_and_daily_budget(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("CONTROL_PLANE_MAX_EXEC_PER_HOUR", "2")
    monkeypatch.setenv("CONTROL_PLANE_MAX_EXEC_PER_DAY", "3")
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
    monkeypatch.setenv("CONTROL_PLANE_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("CONTROL_PLANE_MAX_RETRIES_PER_TASK", "2")
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)

    assert store.noop_decision(role="goal", task_id="T1", signature="sig-1").should_skip is False
    store.record_execution(role="goal", task_id="T1", signature="sig-1", now=now)
    assert store.noop_decision(role="goal", task_id="T1", signature="sig-1").should_skip is True

    assert store.retry_decision(task_id="T1").allowed is True
    store.record_execution(role="goal", task_id="T1", signature="sig-2", now=now + timedelta(minutes=1))
    retry = store.retry_decision(task_id="T1")
    assert retry.allowed is False
    assert retry.attempts == 2
