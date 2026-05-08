# SPDX-License-Identifier: AGPL-3.0-or-later
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


# ---------------------------------------------------------------------------
# Per-backend caps (Option A)
# ---------------------------------------------------------------------------


def test_record_execution_persists_backend_field(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    store.record_execution(
        role="goal", task_id="T1", signature="s", now=now,
        repo_key="r1", backend="archon",
    )
    data = store.load()
    events = [e for e in data["events"] if e.get("kind") == "execution"]
    assert events
    assert events[-1].get("backend") == "archon"
    assert events[-1].get("repo_key") == "r1"


def test_record_execution_omits_backend_when_not_supplied(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    store.record_execution(role="goal", task_id="T1", signature="s", now=now)
    data = store.load()
    events = [e for e in data["events"] if e.get("kind") == "execution"]
    assert events
    assert "backend" not in events[-1]


def test_budget_decision_for_backend_under_cap(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    for i in range(2):
        store.record_execution(
            role="goal", task_id=f"T{i}", signature=f"s{i}",
            now=now - timedelta(minutes=10 * i), backend="archon",
        )
    decision = store.budget_decision_for_backend(
        "archon", max_per_hour=5, max_per_day=20, now=now,
    )
    assert decision.allowed is True


def test_budget_decision_for_backend_hourly_blocks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    for i in range(3):
        store.record_execution(
            role="goal", task_id=f"T{i}", signature=f"s{i}",
            now=now - timedelta(minutes=5 * i), backend="archon",
        )
    decision = store.budget_decision_for_backend(
        "archon", max_per_hour=3, max_per_day=20, now=now,
    )
    assert decision.allowed is False
    assert decision.reason == "backend_budget_exceeded"
    assert decision.window == "hourly"
    assert decision.current == 3
    assert decision.limit == 3


def test_budget_decision_for_backend_daily_blocks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    # Spread across the day, well outside the hourly window
    for i in range(5):
        store.record_execution(
            role="goal", task_id=f"T{i}", signature=f"s{i}",
            now=now - timedelta(hours=2 + i), backend="archon",
        )
    decision = store.budget_decision_for_backend(
        "archon", max_per_hour=10, max_per_day=5, now=now,
    )
    assert decision.allowed is False
    assert decision.window == "daily"
    assert decision.current == 5


def test_budget_decision_for_backend_filters_by_backend(monkeypatch, tmp_path: Path) -> None:
    """Events for other backends do not count against this backend's cap."""
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    # 5 kodo events
    for i in range(5):
        store.record_execution(
            role="goal", task_id=f"K{i}", signature=f"sk{i}",
            now=now - timedelta(minutes=10 * i), backend="kodo",
        )
    # 1 archon event
    store.record_execution(
        role="goal", task_id="A1", signature="sa", now=now - timedelta(minutes=5),
        backend="archon",
    )
    # archon cap of 3 — should be allowed (only 1 archon event counts)
    decision = store.budget_decision_for_backend(
        "archon", max_per_hour=3, max_per_day=10, now=now,
    )
    assert decision.allowed is True


def test_budget_decision_for_backend_no_caps_returns_allowed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    # No caps configured — short-circuit allowed regardless of usage
    for i in range(50):
        store.record_execution(
            role="goal", task_id=f"T{i}", signature=f"s{i}",
            now=now - timedelta(minutes=i), backend="archon",
        )
    assert store.budget_decision_for_backend("archon", now=now).allowed
    assert store.budget_decision_for_backend(
        "archon", max_per_hour=None, max_per_day=None, now=now,
    ).allowed


def test_budget_decision_for_backend_ignores_legacy_unbacked_events(monkeypatch, tmp_path: Path) -> None:
    """Events without a backend field don't count toward any backend's cap.

    This makes the rollout backward-compatible: callers that haven't yet
    been updated to pass `backend=` keep working under the global cap, and
    their events don't accidentally consume the cap of some other backend.
    """
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    # 5 untagged events
    for i in range(5):
        store.record_execution(
            role="goal", task_id=f"T{i}", signature=f"s{i}",
            now=now - timedelta(minutes=10 * i),
        )
    decision = store.budget_decision_for_backend(
        "archon", max_per_hour=2, max_per_day=10, now=now,
    )
    assert decision.allowed is True


def test_backend_cap_settings_pydantic_default():
    from operations_center.config.settings import BackendCapSettings
    cap = BackendCapSettings()
    assert cap.max_per_hour is None
    assert cap.max_per_day is None
    cap2 = BackendCapSettings(max_per_day=10)
    assert cap2.max_per_day == 10


def test_settings_backend_caps_default_empty():
    from operations_center.config.settings import (
        Settings, PlaneSettings, GitSettings, KodoSettings,
    )
    s = Settings(
        plane=PlaneSettings(
            base_url="http://x", api_token_env="X",
            workspace_slug="w", project_id="p",
        ),
        git=GitSettings(),
        kodo=KodoSettings(),
        repos={},
    )
    assert s.backend_caps == {}


# ---------------------------------------------------------------------------
# Naming migration: full rename, no aliases or shims
# ---------------------------------------------------------------------------


def test_record_execution_outcome_persists_backend_and_version(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    store.record_execution_outcome(
        task_id="T1", role="goal", succeeded=True, now=now,
        backend="kodo", backend_version="0.4.272",
    )
    data = store.load()
    ev = next(e for e in data["events"] if e.get("kind") == "execution_outcome")
    assert ev.get("backend") == "kodo"
    assert ev.get("backend_version") == "0.4.272"
    # Old kodo-named field never written by the new code.
    assert "kodo_version" not in ev


def test_record_execution_outcome_kodo_version_kwarg_is_gone(monkeypatch, tmp_path: Path) -> None:
    """Full rename: kodo_version kwarg no longer accepted."""
    import pytest
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    with pytest.raises(TypeError):
        store.record_execution_outcome(
            task_id="T1", role="goal", succeeded=True, now=now,
            kodo_version="0.4.272",   # type: ignore[call-arg]
        )


def test_record_quota_event_writes_quota_event_kind(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    store.record_quota_event(task_id="T1", role="goal", backend="archon", now=now)
    data = store.load()
    ev = next(e for e in data["events"] if e.get("kind") == "quota_event")
    assert ev.get("backend") == "archon"
    # Old kind never written.
    assert not any(e.get("kind") == "kodo_quota_event" for e in data["events"])


def test_record_quota_event_requires_backend(monkeypatch, tmp_path: Path) -> None:
    """Quota exhaustion is meaningless without knowing the backend."""
    import pytest
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    with pytest.raises(TypeError):
        store.record_quota_event(  # type: ignore[call-arg]
            task_id="T1", role="goal", now=now,
        )


def test_record_kodo_quota_event_is_removed(monkeypatch, tmp_path: Path) -> None:
    """Old method name no longer exists on UsageStore."""
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    assert not hasattr(store, "record_kodo_quota_event")


def test_audit_export_uses_quota_event_kind(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    store.record_quota_event(task_id="T1", role="goal", backend="archon", now=now)
    rows = store.audit_export(window_days=1, now=now + timedelta(seconds=1))
    quota_rows = [r for r in rows if r.get("outcome") == "quota_exhausted"]
    assert len(quota_rows) == 1
    assert quota_rows[0].get("backend") == "archon"


def test_circuit_breaker_uses_backend_version_only(monkeypatch, tmp_path: Path) -> None:
    """Mixed-version window blocks the breaker; single-version triggers it.

    Verifies the breaker reads backend_version (no kodo_version fallback).
    """
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_HOUR", "100")
    monkeypatch.setenv("OPERATIONS_CENTER_MAX_EXEC_PER_DAY", "100")
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    # 5 failed outcomes, all same backend_version → breaker should open
    for i in range(5):
        store.record_execution_outcome(
            task_id=f"T{i}", role="goal", succeeded=False,
            now=now - timedelta(minutes=10 * i),
            backend="kodo", backend_version="0.4.272",
        )
    decision = store.budget_decision(now=now)
    assert decision.allowed is False
    assert decision.reason == "circuit_breaker_open"


# ---------------------------------------------------------------------------
# Concurrent-runs tracking + per-backend resource thresholds
# ---------------------------------------------------------------------------


def test_concurrent_runs_starts_at_zero(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    assert store.concurrent_runs_for_backend("kodo", now=now) == 0


def test_concurrent_runs_increments_on_started(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    store.record_execution_started(task_id="T1", backend="archon", now=now)
    store.record_execution_started(task_id="T2", backend="archon", now=now)
    store.record_execution_started(task_id="T3", backend="kodo", now=now)
    assert store.concurrent_runs_for_backend("archon", now=now) == 2
    assert store.concurrent_runs_for_backend("kodo", now=now) == 1
    assert store.concurrent_runs_for_backend("aider", now=now) == 0


def test_concurrent_runs_decrements_on_finished(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    store.record_execution_started(task_id="T1", backend="archon", now=now)
    store.record_execution_started(task_id="T2", backend="archon", now=now)
    store.record_execution_finished(task_id="T1", backend="archon", now=now + timedelta(seconds=30))
    assert store.concurrent_runs_for_backend("archon", now=now + timedelta(seconds=31)) == 1


def test_concurrent_runs_excludes_stale_started_events(
    monkeypatch, tmp_path: Path,
) -> None:
    """A never-finished dispatch from > 24h ago shouldn't deadlock today."""
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    store.record_execution_started(
        task_id="T-stale", backend="kodo", now=now - timedelta(hours=30),
    )
    assert store.concurrent_runs_for_backend("kodo", now=now) == 0


def test_concurrency_decision_for_backend_blocks_at_cap(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    store.record_execution_started(task_id="T1", backend="kodo", now=now)
    store.record_execution_started(task_id="T2", backend="kodo", now=now)
    decision = store.concurrency_decision_for_backend(
        "kodo", max_concurrent=2, now=now,
    )
    assert decision.allowed is False
    assert decision.reason == "backend_concurrency_exceeded"
    assert decision.current == 2
    assert decision.limit == 2


def test_concurrency_decision_for_backend_under_cap(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    store.record_execution_started(task_id="T1", backend="kodo", now=now)
    decision = store.concurrency_decision_for_backend(
        "kodo", max_concurrent=2, now=now,
    )
    assert decision.allowed is True


def test_concurrency_decision_no_cap_returns_allowed(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    for i in range(20):
        store.record_execution_started(task_id=f"T{i}", backend="kodo", now=now)
    assert store.concurrency_decision_for_backend(
        "kodo", max_concurrent=None, now=now,
    ).allowed


def test_memory_decision_no_threshold_returns_allowed(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    assert store.memory_decision_for_backend(
        "kodo", min_available_memory_mb=None, now=now,
    ).allowed


def test_memory_decision_blocks_when_below_threshold(
    monkeypatch, tmp_path: Path,
) -> None:
    """available_memory_mb is monkeypatched low — decision should block."""
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 1024))
    decision = store.memory_decision_for_backend(
        "kodo", min_available_memory_mb=6144, now=now,
    )
    assert decision.allowed is False
    assert decision.reason == "backend_memory_insufficient"
    assert decision.current == 1024
    assert decision.limit == 6144


def test_memory_decision_passes_when_above_threshold(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 32000))
    assert store.memory_decision_for_backend(
        "kodo", min_available_memory_mb=6144, now=now,
    ).allowed


def test_memory_decision_zero_meminfo_returns_allowed(
    monkeypatch, tmp_path: Path,
) -> None:
    """Non-Linux dev box (no /proc/meminfo) shouldn't block dispatch."""
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    store = UsageStore()
    now = datetime(2026, 5, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 0))
    assert store.memory_decision_for_backend(
        "kodo", min_available_memory_mb=6144, now=now,
    ).allowed


def test_backend_cap_settings_resource_fields_default_none():
    from operations_center.config.settings import BackendCapSettings
    cap = BackendCapSettings()
    assert cap.min_available_memory_mb is None
    assert cap.max_concurrent is None
    cap2 = BackendCapSettings(
        max_per_day=5, min_available_memory_mb=8192, max_concurrent=4,
    )
    assert cap2.min_available_memory_mb == 8192
    assert cap2.max_concurrent == 4
