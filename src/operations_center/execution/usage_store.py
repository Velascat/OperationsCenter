# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
import os
import shutil
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Generator, cast

from operations_center.execution.models import BudgetDecision, ExecutionControlSettings, NoOpDecision, RetryDecision

# ---------------------------------------------------------------------------
# Module-level path-keyed threading locks.
# All UsageStore instances that share the same on-disk path share a lock so
# that load-modify-save triples are atomic even when concurrent dispatchers
# write to the same usage.json.
# ---------------------------------------------------------------------------
_path_locks: dict[str, threading.RLock] = {}
_meta_lock = threading.Lock()


def _get_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _meta_lock:
        if key not in _path_locks:
            _path_locks[key] = threading.RLock()
        return _path_locks[key]


# ---------------------------------------------------------------------------
# Circuit-breaker constants (configurable via env).
# ---------------------------------------------------------------------------
_CB_THRESHOLD = float(os.environ.get("OPERATIONS_CENTER_CIRCUIT_BREAKER_THRESHOLD", "0.8"))
_CB_WINDOW = max(3, int(os.environ.get("OPERATIONS_CENTER_CIRCUIT_BREAKER_WINDOW", "5")))
# If ALL outcomes in the window are older than this many hours, auto-recover.
# Prevents indefinite deadlock from historical failures after the underlying
# issue has been resolved and the operator hasn't manually cleared usage.json.
_CB_STALENESS_HOURS = float(os.environ.get("OPERATIONS_CENTER_CIRCUIT_BREAKER_STALENESS_HOURS", "4"))

# ---------------------------------------------------------------------------
# Disk-space guardrail constants.
# ---------------------------------------------------------------------------
_DISK_WARN_MB = 200   # Log a warning below this threshold
_DISK_MIN_MB = 50     # Raise OSError below this threshold (avoids partial writes)


def _check_disk_space(path: Path) -> None:
    """Raise OSError when free space near *path* is critically low.

    Logs a structured warning when space is low but above the hard minimum.
    This prevents the watcher from entering a crash-restart loop caused by
    failed artifact writes when disk fills between janitor runs.
    """
    try:
        free_mb = shutil.disk_usage(path.parent if not path.is_dir() else path).free / (1024 * 1024)
    except OSError:
        return  # Can't check — don't block the write
    if free_mb < _DISK_MIN_MB:
        raise OSError(
            f"disk_space_critical: only {free_mb:.0f} MB free near {path} "
            f"(minimum {_DISK_MIN_MB} MB required). "
            "Run 'operations-center.sh janitor' or free disk space before retrying."
        )
    # Warn (non-fatal) when space is getting low
    if free_mb < _DISK_WARN_MB:
        import logging
        logging.getLogger(__name__).warning(
            '{"event": "disk_space_low", "free_mb": %.0f, "warn_threshold_mb": %d, "path": "%s"}',
            free_mb, _DISK_WARN_MB, path,
        )


class UsageStore:
    def __init__(self, path: Path | None = None) -> None:
        self.settings = ExecutionControlSettings.from_env()
        self.path = path or self.settings.usage_path

    @contextmanager
    def _exclusive(self) -> Generator[None, None, None]:
        """Acquire an exclusive per-path reentrant lock for load-modify-save."""
        with _get_lock(self.path):
            yield

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "updated_at": None,
                "events": [],
                "task_attempts": {},
                "last_task_signatures": {},
                "task_artifacts": {},
                "hourly_exec_count": 0,
                "daily_exec_count": 0,
                "skipped_due_to_budget": 0,
                "skipped_due_to_noop": 0,
                "skipped_due_to_cooldown": 0,
                "blocked_due_to_retry_cap": 0,
                "suppressed_due_to_proposal_budget": 0,
            }
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any], *, now: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _check_disk_space(self.path)
        events = self._prune_events(list(data.get("events", [])), now=now)
        data["events"] = events
        data["updated_at"] = now.isoformat()
        counts = Counter(event.get("kind") for event in events)
        data["hourly_exec_count"] = self._exec_count(events, since=now - timedelta(hours=1))
        data["daily_exec_count"] = self._exec_count(events, since=now - timedelta(days=1))
        data["skipped_due_to_budget"] = counts.get("skip_budget", 0)
        data["skipped_due_to_noop"] = counts.get("skip_noop", 0)
        data["skipped_due_to_cooldown"] = counts.get("skip_cooldown", 0)
        data["blocked_due_to_retry_cap"] = counts.get("retry_cap_block", 0)
        data["suppressed_due_to_proposal_budget"] = counts.get("proposal_budget_suppressed", 0)
        # Atomic write: write to a temp file then rename (rename is atomic on Linux).
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def budget_decision(self, *, now: datetime) -> BudgetDecision:
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        hourly = self._exec_count(events, since=now - timedelta(hours=1))
        if hourly >= self.settings.max_exec_per_hour:
            return BudgetDecision(
                allowed=False,
                reason="budget_exceeded",
                window="hourly",
                limit=self.settings.max_exec_per_hour,
                current=hourly,
            )
        daily = self._exec_count(events, since=now - timedelta(days=1))
        if daily >= self.settings.max_exec_per_day:
            return BudgetDecision(
                allowed=False,
                reason="budget_exceeded",
                window="daily",
                limit=self.settings.max_exec_per_day,
                current=daily,
            )
        # Circuit breaker: if ≥ threshold fraction of last _CB_WINDOW *fresh*
        # execution outcomes failed, pause execution until the operator investigates.
        # "Fresh" means within the last _CB_STALENESS_HOURS — stale failures age
        # out of the window naturally rather than blocking indefinitely.
        # Requires at least 3 fresh samples to avoid false positives at startup.
        # If a backend binary was upgraded during the window (multiple versions
        # in the window), skip the circuit breaker — failures from the old
        # version should not block the newly deployed version.
        stale_cutoff = now - timedelta(hours=_CB_STALENESS_HOURS)
        outcomes = [
            e for e in reversed(events)
            if e.get("kind") == "execution_outcome"
            and datetime.fromisoformat(e["timestamp"]) > stale_cutoff
        ][:_CB_WINDOW]
        if len(outcomes) >= 3:
            versions_in_window = {
                str(e["backend_version"]) for e in outcomes if e.get("backend_version")
            }
            if len(versions_in_window) <= 1:  # only block if all outcomes same version
                failures = sum(1 for e in outcomes if not e.get("succeeded"))
                if failures / len(outcomes) >= _CB_THRESHOLD:
                    return BudgetDecision(
                        allowed=False,
                        reason="circuit_breaker_open",
                        window="recent",
                        limit=_CB_WINDOW,
                        current=failures,
                    )
        return BudgetDecision(allowed=True)

    def remaining_exec_capacity(self, *, now: datetime) -> int:
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        hourly = self.settings.max_exec_per_hour - self._exec_count(events, since=now - timedelta(hours=1))
        daily = self.settings.max_exec_per_day - self._exec_count(events, since=now - timedelta(days=1))
        return min(hourly, daily)

    def retry_decision(self, *, task_id: str, now: datetime | None = None) -> RetryDecision:
        data = self.load()
        attempts = int(data.get("task_attempts", {}).get(task_id, 0))
        if attempts >= self.settings.max_retries_per_task:
            # Auto-reset if the last attempt was >1h ago — indicates a human manually
            # unblocked the task and expects another try with a clean slate.
            last_attempt_ts = self._last_attempt_timestamp(data, task_id)
            ref = now or datetime.now(UTC)
            if last_attempt_ts is None or (ref - last_attempt_ts) > timedelta(hours=1):
                self._reset_task_attempts(data, task_id)
                attempts = 0
            else:
                return RetryDecision(
                    allowed=False,
                    reason="retry_cap_exceeded",
                    attempts=attempts,
                    limit=self.settings.max_retries_per_task,
                )
        return RetryDecision(allowed=True, attempts=attempts, limit=self.settings.max_retries_per_task)

    def _last_attempt_timestamp(self, data: dict[str, object], task_id: str) -> datetime | None:
        """Return the timestamp of the most recent execution event for this task_id."""
        events = data.get("events", [])
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            ev = cast("dict[str, object]", event)
            if ev.get("task_id") == task_id and ev.get("kind") in ("execution", "retry_cap_block"):
                ts = ev.get("timestamp")
                if ts:
                    try:
                        from datetime import timezone
                        dt = datetime.fromisoformat(str(ts))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt
                    except ValueError:
                        pass
        return None

    def _reset_task_attempts(self, data: dict[str, Any], task_id: str) -> None:
        """Clear attempt count and last signature for task_id, then persist."""
        attempts = dict(data.get("task_attempts", {}))
        attempts.pop(task_id, None)
        data["task_attempts"] = attempts
        sigs = dict(data.get("last_task_signatures", {}))
        for key in list(sigs.keys()):
            if key.endswith(f":{task_id}"):
                del sigs[key]
        data["last_task_signatures"] = sigs
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def noop_decision(
        self,
        *,
        role: str,
        task_id: str,
        signature: str,
    ) -> NoOpDecision:
        data = self.load()
        last = str(data.get("last_task_signatures", {}).get(f"{role}:{task_id}", "")).strip()
        if last and last == signature:
            return NoOpDecision(should_skip=True, reason="no_op", detail="no_state_change")
        return NoOpDecision(should_skip=False)

    def record_execution(
        self,
        *,
        role: str,
        task_id: str,
        signature: str,
        now: datetime,
        repo_key: str | None = None,
        backend: str | None = None,
    ) -> None:
        """Record one execution event.

        ``backend`` (e.g. ``"kodo"``, ``"archon"``, ``"aider"``) is optional
        for backward compatibility but should be supplied by all new callers.
        It feeds ``budget_decision_for_backend()`` so the per-backend caps in
        ``Settings.backend_caps`` can be enforced. Without it, only the
        global hourly/daily and per-repo caps apply.
        """
        with self._exclusive():
            data = self.load()
            attempts = dict(data.get("task_attempts", {}))
            attempts[task_id] = int(attempts.get(task_id, 0)) + 1
            data["task_attempts"] = attempts
            signatures = dict(data.get("last_task_signatures", {}))
            signatures[f"{role}:{task_id}"] = signature
            data["last_task_signatures"] = signatures
            event: dict[str, Any] = {
                "kind": "execution",
                "role": role,
                "task_id": task_id,
                "signature": signature,
                "timestamp": now.isoformat(),
            }
            if repo_key:
                event["repo_key"] = repo_key
            if backend:
                event["backend"] = backend
            self._append_event(data, event, now=now)

    def record_execution_outcome(
        self,
        *,
        task_id: str,
        role: str,
        succeeded: bool,
        now: datetime,
        backend: str | None = None,
        backend_version: str | None = None,
    ) -> None:
        """Record whether a goal/test execution produced a successful result.

        These events feed the circuit breaker in ``budget_decision``. Record
        after the task handler has determined the final outcome — not before.

        ``backend`` (e.g. ``"kodo"``, ``"archon"``) tags which backend ran
        the work. ``backend_version`` is the binary/image version; the
        circuit breaker uses it to skip version-transition windows from
        failure-rate calculations (failures from the old version don't
        block the newly deployed one).
        """
        with self._exclusive():
            data = self.load()
            event: dict[str, Any] = {
                "kind": "execution_outcome",
                "task_id": task_id,
                "role": role,
                "succeeded": succeeded,
                "timestamp": now.isoformat(),
            }
            if backend:
                event["backend"] = backend
            if backend_version:
                event["backend_version"] = backend_version
            self._append_event(data, event, now=now)

    def record_quality_warning(
        self,
        *,
        task_id: str,
        repo_key: str,
        suppression_counts: dict[str, int],
        now: datetime,
    ) -> None:
        """Record a kodo quality-erosion warning for the given task.

        Quality warnings are emitted when a kodo run adds an above-threshold
        number of inline suppressions (``# noqa``, ``# type: ignore``, bare
        ``pass`` in test bodies).  These pass validation but erode code quality
        over time.  Recording them provides a queryable signal for operators and
        the self-tuning regulator.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "kodo_quality_warning",
                    "task_id": task_id,
                    "repo_key": repo_key,
                    "suppression_counts": suppression_counts,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def record_scope_violation(
        self,
        *,
        task_id: str,
        repo_key: str,
        violated_files: list[str],
        now: datetime,
    ) -> None:
        """Record a scope-policy violation for observability.

        Scope violations occur when kodo modifies files outside the task's
        ``allowed_paths`` after both the initial run and the policy-retry pass.
        Recording them enables the improve watcher and operators to detect
        patterns (e.g. a task family that consistently escapes its scope) via
        the usage store events.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "scope_violation",
                    "task_id": task_id,
                    "repo_key": repo_key,
                    "violated_files": violated_files[:10],  # cap to avoid bloat
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def record_quota_event(
        self,
        *,
        task_id: str,
        role: str,
        backend: str,
        now: datetime,
    ) -> None:
        """Record a hard quota-exhaustion event from a backend.

        Unlike ``execution_outcome`` failures, quota events do NOT feed the
        circuit breaker — they are an infrastructure problem (rate-limited
        API key, monthly cap exhausted, billing issue), not a task-quality
        signal. The operator must top up credits or wait for a reset.

        ``backend`` (e.g. ``"kodo"``, ``"archon"``) is required so
        ``audit_export`` and per-backend reporting can attribute the quota
        hit; quota exhaustion is meaningless without knowing which backend.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "quota_event",
                    "task_id": task_id,
                    "role": role,
                    "backend": backend,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    # ---------------------------------------------------------------------------
    # S6-2: Per-repo execution budget
    # ---------------------------------------------------------------------------

    def budget_decision_for_repo(
        self,
        repo_key: str,
        max_daily: int,
        *,
        now: datetime,
    ) -> "BudgetDecision":
        """Return a BudgetDecision for a specific repo's daily execution cap.

        Unlike the global budget, this only counts ``execution`` events for the
        given ``repo_key``.  Callers should check this *after* the global
        budget passes.
        """
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        cutoff = now - timedelta(days=1)
        repo_count = 0
        for e in events:
            if e.get("kind") != "execution":
                continue
            if e.get("repo_key") != repo_key:
                continue
            try:
                ts = datetime.fromisoformat(str(e["timestamp"]))
            except (ValueError, KeyError):
                continue
            if ts >= cutoff:
                repo_count += 1
        if repo_count >= max_daily:
            return BudgetDecision(
                allowed=False,
                reason="repo_budget_exceeded",
                window="daily",
                limit=max_daily,
                current=repo_count,
            )
        return BudgetDecision(allowed=True)

    # ---------------------------------------------------------------------------
    # Per-backend execution budget (mirrors per-repo, additive)
    # ---------------------------------------------------------------------------

    def budget_decision_for_backend(
        self,
        backend: str,
        *,
        max_per_hour: int | None = None,
        max_per_day: int | None = None,
        now: datetime,
    ) -> "BudgetDecision":
        """Return a BudgetDecision for one backend's hourly/daily cap.

        Counts only ``execution`` events whose ``backend`` field matches.
        Either limit may be ``None`` to skip that window. Returns ``allowed``
        when both windows pass (or when both limits are None — the
        backend has no per-backend cap configured).

        Callers should check this *after* the global budget passes
        (``budget_decision()``) and *after* the per-repo cap if
        applicable. Backends with no entry in
        ``Settings.backend_caps`` are unconstrained at this layer —
        only the global cap applies.

        Events recorded *before* the ``backend`` field was added simply
        don't match this filter; they continue to count toward the
        global cap but not toward any per-backend limit. This makes the
        rollout backward-compatible: callers can adopt the new field
        opportunistically.
        """
        if not backend:
            return BudgetDecision(allowed=True)
        if max_per_hour is None and max_per_day is None:
            return BudgetDecision(allowed=True)
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        cutoff_hour = now - timedelta(hours=1)
        cutoff_day = now - timedelta(days=1)
        hourly = 0
        daily = 0
        for e in events:
            if e.get("kind") != "execution":
                continue
            if e.get("backend") != backend:
                continue
            try:
                ts = datetime.fromisoformat(str(e["timestamp"]))
            except (ValueError, KeyError):
                continue
            if ts >= cutoff_day:
                daily += 1
                if ts >= cutoff_hour:
                    hourly += 1
        if max_per_hour is not None and hourly >= max_per_hour:
            return BudgetDecision(
                allowed=False,
                reason="backend_budget_exceeded",
                window="hourly",
                limit=max_per_hour,
                current=hourly,
            )
        if max_per_day is not None and daily >= max_per_day:
            return BudgetDecision(
                allowed=False,
                reason="backend_budget_exceeded",
                window="daily",
                limit=max_per_day,
                current=daily,
            )
        return BudgetDecision(allowed=True)

    # ---------------------------------------------------------------------------
    # Concurrency tracking + per-backend resource thresholds
    # ---------------------------------------------------------------------------

    def record_execution_started(
        self,
        *,
        task_id: str,
        backend: str,
        now: datetime,
    ) -> None:
        """Record that a dispatch has started (for concurrency accounting).

        Pair with ``record_execution_finished`` after the dispatch returns.
        Concurrency is computed as ``execution_started`` events whose
        matching ``execution_finished`` hasn't been seen yet, scoped to a
        backend.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "execution_started",
                    "task_id": task_id,
                    "backend": backend,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def record_execution_finished(
        self,
        *,
        task_id: str,
        backend: str,
        now: datetime,
    ) -> None:
        """Record that a dispatch has finished (paired with started).

        The pair (``task_id``, ``backend``) identifies which started event
        this finished event closes.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "execution_finished",
                    "task_id": task_id,
                    "backend": backend,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def concurrent_runs_for_backend(
        self,
        backend: str,
        *,
        now: datetime,
    ) -> int:
        """Return how many ``backend`` dispatches are currently in flight.

        Counts ``execution_started`` events whose matching
        ``execution_finished`` (same task_id + backend) has not been seen.
        Stale events older than 24h are excluded — a never-finished
        dispatch from yesterday shouldn't deadlock today's quota.
        """
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        cutoff = now - timedelta(hours=24)
        in_flight: set[str] = set()
        for e in events:
            ts_raw = e.get("timestamp")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            if ts < cutoff:
                continue
            if e.get("backend") != backend:
                continue
            tid = e.get("task_id")
            if not isinstance(tid, str):
                continue
            kind = e.get("kind")
            if kind == "execution_started":
                in_flight.add(tid)
            elif kind == "execution_finished":
                in_flight.discard(tid)
        return len(in_flight)

    def total_concurrent_runs(self, *, now: datetime) -> int:
        """In-flight dispatches across all backends.

        Same algorithm as ``concurrent_runs_for_backend`` without the
        backend filter — used by the global resource gate that reserves
        host headroom for co-tenant workloads regardless of which OC
        backend is dispatching.
        """
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        cutoff = now - timedelta(hours=24)
        in_flight: set[tuple[str, str]] = set()
        for e in events:
            ts_raw = e.get("timestamp")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            if ts < cutoff:
                continue
            tid = e.get("task_id")
            backend = e.get("backend") or ""
            if not isinstance(tid, str):
                continue
            kind = e.get("kind")
            if kind == "execution_started":
                in_flight.add((backend, tid))
            elif kind == "execution_finished":
                in_flight.discard((backend, tid))
        return len(in_flight)

    def global_concurrency_decision(
        self,
        *,
        max_concurrent: int | None,
        now: datetime,
    ) -> "BudgetDecision":
        """Block when total in-flight runs are at the global cap."""
        if max_concurrent is None:
            return BudgetDecision(allowed=True)
        in_flight = self.total_concurrent_runs(now=now)
        if in_flight >= max_concurrent:
            return BudgetDecision(
                allowed=False,
                reason="global_concurrency_exceeded",
                window="in_flight",
                limit=max_concurrent,
                current=in_flight,
            )
        return BudgetDecision(allowed=True)

    def global_memory_decision(
        self,
        *,
        min_available_memory_mb: int | None,
    ) -> "BudgetDecision":
        """Block when free RAM is below the global headroom floor.

        Mirrors ``memory_decision_for_backend`` without a backend tag —
        the gate reserves memory for co-tenant workloads on the same
        host regardless of which backend is dispatching. Returns allowed
        when ``/proc/meminfo`` is unreadable (non-Linux dev box).
        """
        if min_available_memory_mb is None:
            return BudgetDecision(allowed=True)
        avail = self.available_memory_mb()
        if avail == 0:
            return BudgetDecision(allowed=True)
        if avail < min_available_memory_mb:
            return BudgetDecision(
                allowed=False,
                reason="global_memory_insufficient",
                window="instant",
                limit=min_available_memory_mb,
                current=avail,
            )
        return BudgetDecision(allowed=True)

    def concurrency_decision_for_backend(
        self,
        backend: str,
        *,
        max_concurrent: int | None,
        now: datetime,
    ) -> "BudgetDecision":
        """Block when ``concurrent_runs_for_backend(backend)`` is at the cap."""
        if not backend or max_concurrent is None:
            return BudgetDecision(allowed=True)
        in_flight = self.concurrent_runs_for_backend(backend, now=now)
        if in_flight >= max_concurrent:
            return BudgetDecision(
                allowed=False,
                reason="backend_concurrency_exceeded",
                window="in_flight",
                limit=max_concurrent,
                current=in_flight,
            )
        return BudgetDecision(allowed=True)

    @staticmethod
    def available_memory_mb() -> int:
        """Return free RAM in MB by reading /proc/meminfo. 0 on non-Linux."""
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        # "MemAvailable:   12345 kB"
                        parts = line.split()
                        if len(parts) >= 2:
                            try:
                                return int(parts[1]) // 1024
                            except ValueError:
                                return 0
        except OSError:
            pass
        return 0

    def memory_decision_for_backend(
        self,
        backend: str,
        *,
        min_available_memory_mb: int | None,
        now: datetime,
    ) -> "BudgetDecision":
        """Block when free RAM is below ``min_available_memory_mb``.

        ``now`` is unused (RAM is read fresh from /proc) but kept in the
        signature for symmetry with the other ``*_decision_for_backend``
        helpers so callers can chain them uniformly.
        """
        del now  # symmetry-only param; RAM is read live
        if not backend or min_available_memory_mb is None:
            return BudgetDecision(allowed=True)
        avail = self.available_memory_mb()
        if avail == 0:
            # Couldn't read /proc/meminfo (non-Linux dev box). Don't block —
            # the operator's machine isn't the production environment.
            return BudgetDecision(allowed=True)
        if avail < min_available_memory_mb:
            return BudgetDecision(
                allowed=False,
                reason="backend_memory_insufficient",
                window="instant",
                limit=min_available_memory_mb,
                current=avail,
            )
        return BudgetDecision(allowed=True)

    # ---------------------------------------------------------------------------
    # S6-4: Gradual failure rate degradation detection
    # ---------------------------------------------------------------------------

    def check_failure_rate_degradation(
        self,
        *,
        window: int = 30,
        warn_threshold: float = 0.6,
        now: datetime,
    ) -> float | None:
        """Return the recent success rate when it has degraded below *warn_threshold*.

        Returns ``None`` when the sample count is too low (< 5) or success rate
        is healthy.  Returns the success rate (< warn_threshold) when degraded,
        so callers can log or act on it.

        This is a *warning* signal — it fires before the circuit breaker
        (which opens at ≥80% failure), giving operators earlier notice.
        """
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        outcomes = [
            e for e in reversed(events)
            if e.get("kind") == "execution_outcome"
        ][:window]
        if len(outcomes) < 5:
            return None
        successes = sum(1 for e in outcomes if e.get("succeeded"))
        rate = successes / len(outcomes)
        return rate if rate < warn_threshold else None

    # ---------------------------------------------------------------------------
    # S6-5: Execution duration baseline
    # ---------------------------------------------------------------------------

    def record_execution_duration(
        self,
        *,
        task_id: str,
        role: str,
        duration_seconds: float,
        now: datetime,
    ) -> None:
        """Record the wall-clock duration of a kodo execution pass.

        Used to build a baseline that can detect abnormally long runs (e.g. kodo
        stuck in a loop) before the stale-running TTL fires.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "execution_duration",
                    "task_id": task_id,
                    "role": role,
                    "duration_seconds": round(duration_seconds, 1),
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def median_execution_duration(
        self,
        role: str,
        *,
        window: int = 20,
        now: datetime | None = None,
    ) -> float | None:
        """Return the median execution duration in seconds for *role*.

        Returns ``None`` if fewer than 3 samples exist.
        """
        from datetime import timezone as _tz
        _now = now or datetime.now(_tz.utc)
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=_now)
        durations = [
            float(e["duration_seconds"])
            for e in reversed(events)
            if e.get("kind") == "execution_duration"
            and e.get("role") == role
            and isinstance(e.get("duration_seconds"), (int, float))
        ][:window]
        if len(durations) < 3:
            return None
        sorted_d = sorted(durations)
        mid = len(sorted_d) // 2
        if len(sorted_d) % 2 == 1:
            return sorted_d[mid]
        return (sorted_d[mid - 1] + sorted_d[mid]) / 2

    # ---------------------------------------------------------------------------
    # S6-9: Structured audit log export
    # ---------------------------------------------------------------------------

    def audit_export(
        self,
        *,
        window_days: int = 7,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return a structured activity log for the last *window_days* days.

        Each entry contains the fields most useful for human or machine audit:
        task_id, role, outcome (succeeded/failed/quota/quality/scope), repo_key,
        duration_seconds, and timestamp.  Events are sorted oldest-first.

        Consumers can filter by role, repo_key, or outcome to answer questions
        like "what did the system do to repo X this week?" without reading raw
        JSON events.
        """
        _now = now or datetime.now(UTC)
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=_now)
        cutoff = _now - timedelta(days=window_days)

        # Build a map of task_id → duration from execution_duration events
        durations: dict[str, float] = {}
        for e in events:
            if e.get("kind") == "execution_duration" and e.get("task_id"):
                durations[str(e["task_id"])] = float(e.get("duration_seconds", 0))

        # Collect and flatten meaningful event kinds
        audit_rows: list[dict[str, Any]] = []
        for e in events:
            kind = e.get("kind", "")
            try:
                ts = datetime.fromisoformat(str(e["timestamp"]))
            except (ValueError, KeyError):
                continue
            if ts < cutoff:
                continue

            if kind == "execution_outcome":
                audit_rows.append({
                    "kind": "execution",
                    "task_id": e.get("task_id", ""),
                    "role": e.get("role", ""),
                    "outcome": "succeeded" if e.get("succeeded") else "failed",
                    "repo_key": e.get("repo_key", ""),
                    "duration_seconds": durations.get(str(e.get("task_id", "")), None),
                    "backend": e.get("backend", ""),
                    "backend_version": e.get("backend_version", None),
                    "timestamp": e["timestamp"],
                })
            elif kind == "quota_event":
                audit_rows.append({
                    "kind": "execution",
                    "task_id": e.get("task_id", ""),
                    "role": e.get("role", ""),
                    "outcome": "quota_exhausted",
                    "repo_key": "",
                    "backend": e.get("backend", ""),
                    "duration_seconds": None,
                    "timestamp": e["timestamp"],
                })
            elif kind == "kodo_quality_warning":
                audit_rows.append({
                    "kind": "quality_warning",
                    "task_id": e.get("task_id", ""),
                    "role": "",
                    "outcome": "quality_warning",
                    "repo_key": e.get("repo_key", ""),
                    "suppression_counts": e.get("suppression_counts", {}),
                    "timestamp": e["timestamp"],
                })
            elif kind == "scope_violation":
                audit_rows.append({
                    "kind": "scope_violation",
                    "task_id": e.get("task_id", ""),
                    "role": "",
                    "outcome": "scope_violation",
                    "repo_key": e.get("repo_key", ""),
                    "violated_files": e.get("violated_files", []),
                    "timestamp": e["timestamp"],
                })
            elif kind == "escalation_sent":
                audit_rows.append({
                    "kind": "escalation",
                    "task_id": "",
                    "role": "",
                    "outcome": "escalated",
                    "repo_key": "",
                    "classification": e.get("classification", ""),
                    "task_ids": e.get("task_ids", []),
                    "timestamp": e["timestamp"],
                })

        audit_rows.sort(key=lambda r: r["timestamp"])
        return audit_rows

    def record_skip(
        self,
        *,
        role: str,
        task_id: str,
        signature: str,
        reason: str,
        detail: str | None,
        now: datetime,
        evidence: dict[str, object] | None = None,
    ) -> None:
        with self._exclusive():
            data = self.load()
            # Only persist the signature for genuine no-op skips (kodo ran and
            # made no changes).  Budget, cooldown, and kodo-gate skips must NOT
            # update last_task_signatures — they mean "worker was busy, try
            # again later", not "task is already satisfied".  Storing the
            # signature for non-noop skips causes a false noop match on the
            # very next cycle (same updated_at + empty description = same sig).
            if reason == "no_op":
                signatures = dict(data.get("last_task_signatures", {}))
                signatures[f"{role}:{task_id}"] = signature
                data["last_task_signatures"] = signatures
            kind = "skip_noop" if reason == "no_op" else "skip_budget"
            if reason == "cooldown_active":
                kind = "skip_cooldown"
            self._append_event(
                data,
                {
                    "kind": kind,
                    "role": role,
                    "task_id": task_id,
                    "signature": signature,
                    "reason": reason,
                    "detail": detail,
                    "evidence": evidence or {},
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def record_retry_cap(self, *, role: str, task_id: str, now: datetime, attempts: int, limit: int) -> None:
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "retry_cap_block",
                    "role": role,
                    "task_id": task_id,
                    "attempts": attempts,
                    "limit": limit,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def record_proposal_cycle(
        self,
        *,
        created: int,
        deduped: int,
        skipped: int,
        now: datetime,
    ) -> None:
        """Record one proposal cycle outcome for satiation tracking.

        ``created``  — new tasks actually created this cycle.
        ``deduped``  — proposals that already existed on the board.
        ``skipped``  — proposals skipped due to conflict or focus-area gate.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "proposal_cycle",
                    "created": created,
                    "deduped": deduped,
                    "skipped": skipped,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def is_proposal_satiated(
        self,
        *,
        now: datetime,
        window_cycles: int = 5,
        dedup_ratio_threshold: float = 0.9,
    ) -> bool:
        """Return True if the last *window_cycles* proposal cycles produced nothing new.

        When the dedup+skipped fraction of all proposals across the window
        exceeds *dedup_ratio_threshold* the repo is considered stable and no
        further proposals are needed until something changes externally.
        """
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        recent = [e for e in reversed(events) if e.get("kind") == "proposal_cycle"][:window_cycles]
        if len(recent) < window_cycles:
            return False
        total_created = sum(int(e.get("created", 0)) for e in recent)
        total_deduped = sum(int(e.get("deduped", 0)) for e in recent)
        total_skipped = sum(int(e.get("skipped", 0)) for e in recent)
        total = total_created + total_deduped + total_skipped
        if total == 0:
            return False
        if total_created > 0:
            return False
        return (total_deduped + total_skipped) / total >= dedup_ratio_threshold

    def reset_satiation_window(self, *, now: datetime) -> None:
        """Remove proposal_cycle events so the satiation window starts fresh.

        Called when the board fully drains (active_count == 0) so the proposer
        can re-evaluate immediately after tasks complete rather than staying
        silent until an external autonomy-cycle refresh.
        """
        del now  # reserved for future time-bounded reset (currently removes all proposal_cycle events)
        with self._exclusive():
            data = self.load()
            data["events"] = [
                e for e in data.get("events", []) if e.get("kind") != "proposal_cycle"
            ]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def record_proposal_outcome(self, *, category: str, succeeded: bool, now: datetime) -> None:
        """Record whether a task of a given category succeeded or failed."""
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "proposal_outcome",
                    "category": category,
                    "succeeded": succeeded,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def proposal_success_rate(
        self,
        category: str,
        *,
        window: int = 20,
        now: datetime | None = None,
    ) -> float:
        """Return success rate for *category* over the last *window* outcomes.

        Returns 0.5 (neutral) if fewer than 3 samples exist.
        """
        now = now or datetime.now(UTC)
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        outcomes = [
            e for e in reversed(events)
            if e.get("kind") == "proposal_outcome" and e.get("category") == category
        ][:window]
        if len(outcomes) < 3:
            return 0.5
        successes = sum(1 for e in outcomes if e.get("succeeded"))
        return successes / len(outcomes)

    def record_validation_outcome(self, *, command: str, passed: bool, now: datetime) -> None:
        """Append a validation_outcome event for flaky-test tracking."""
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "validation_outcome",
                    "command": command,
                    "passed": passed,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def is_command_flaky(
        self,
        command: str,
        *,
        window: int = 10,
        fail_ratio: float = 0.3,
        now: datetime | None = None,
    ) -> bool:
        """Return True if >= *fail_ratio* of the last *window* runs for *command* failed.

        Requires at least *window* samples before returning True to avoid
        false-positives on new commands.
        """
        now = now or datetime.now(UTC)
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        outcomes = [
            e for e in reversed(events)
            if e.get("kind") == "validation_outcome" and e.get("command") == command
        ][:window]
        if len(outcomes) < window:
            return False
        failures = sum(1 for e in outcomes if not e.get("passed"))
        return (failures / window) >= fail_ratio

    def record_escalation(self, *, classification: str, task_ids: list[str], now: datetime) -> None:
        """Record that an escalation webhook was fired for *classification*."""
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "escalation_sent",
                    "classification": classification,
                    "task_ids": task_ids,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def should_escalate(
        self,
        *,
        classification: str,
        threshold: int,
        cooldown_seconds: int,
        window_seconds: int = 86400,
        now: datetime,
    ) -> tuple[bool, list[str]]:
        """Return ``(True, matching_task_ids)`` when escalation should fire.

        Fires when there are at least *threshold* ``blocked_triage`` events with
        *classification* within *window_seconds* AND no ``escalation_sent`` event
        for this classification within *cooldown_seconds*.
        """
        from datetime import timezone, timedelta

        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        window_start = now - timedelta(seconds=window_seconds)
        cooldown_start = now - timedelta(seconds=cooldown_seconds)

        # Check cooldown
        for ev in reversed(events):
            if ev.get("kind") != "escalation_sent":
                continue
            if ev.get("classification") != classification:
                continue
            try:
                ts = datetime.fromisoformat(str(ev["timestamp"]))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cooldown_start:
                    return False, []
            except (ValueError, KeyError):
                pass

        # Count matching block events in window
        matching_ids: list[str] = []
        for ev in events:
            if ev.get("kind") != "blocked_triage":
                continue
            if ev.get("classification") != classification:
                continue
            try:
                ts = datetime.fromisoformat(str(ev["timestamp"]))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= window_start:
                    tid = str(ev.get("task_id", ""))
                    if tid:
                        matching_ids.append(tid)
            except (ValueError, KeyError):
                pass

        if len(matching_ids) >= threshold:
            return True, matching_ids
        return False, []

    def consecutive_blocks_for_task(self, task_id: str, *, now: datetime) -> int:
        """Return how many consecutive blocked_triage events exist for *task_id*.

        Counts backwards from the most recent event until a non-blocked event
        (execution_outcome with succeeded=True) is found or all events are scanned.
        Used by S7-4 self-healing to detect tasks stuck in a block loop.
        """
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=now)
        count = 0
        for ev in reversed(events):
            if ev.get("task_id") != task_id:
                continue
            kind = ev.get("kind")
            if kind == "blocked_triage":
                count += 1
            elif kind == "execution_outcome" and ev.get("succeeded"):
                break
        return count

    def record_blocked_triage(self, *, task_id: str, classification: str, now: datetime) -> None:
        """Record a single blocked-triage event for escalation tracking."""
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "blocked_triage",
                    "task_id": task_id,
                    "classification": classification,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def record_execution_cost(
        self,
        *,
        task_id: str,
        repo_key: str,
        estimated_usd: float,
        now: datetime,
    ) -> None:
        """Append an execution_cost event for spend telemetry.

        *estimated_usd* is a caller-supplied estimate (e.g. from
        ``Settings.cost_per_execution_usd``).  Zero is valid and means cost
        tracking is disabled for this repo.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "execution_cost",
                    "task_id": task_id,
                    "repo_key": repo_key,
                    "estimated_usd": estimated_usd,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def get_spend_report(self, *, window_days: int = 1, now: datetime | None = None) -> dict[str, Any]:
        """Return a spend summary for the last *window_days* days.

        Returns::

            {
                "window_days": int,
                "total_executions": int,
                "total_estimated_usd": float,
                "per_repo": {
                    "<repo_key>": {
                        "executions": int,
                        "estimated_usd": float,
                    },
                    ...
                },
            }
        """
        _now = now or datetime.now(UTC)
        data = self.load()
        events = self._prune_events(list(data.get("events", [])), now=_now)
        cutoff = _now - timedelta(days=window_days)
        per_repo: dict[str, dict[str, Any]] = {}
        total_executions = 0
        total_usd = 0.0
        for ev in events:
            if ev.get("kind") != "execution_cost":
                continue
            try:
                ts = datetime.fromisoformat(str(ev["timestamp"]))
            except (ValueError, KeyError):
                continue
            if ts < cutoff:
                continue
            repo_key = str(ev.get("repo_key") or "unknown")
            usd = float(ev.get("estimated_usd") or 0.0)
            bucket = per_repo.setdefault(repo_key, {"executions": 0, "estimated_usd": 0.0})
            bucket["executions"] += 1
            bucket["estimated_usd"] = round(bucket["estimated_usd"] + usd, 6)
            total_executions += 1
            total_usd += usd
        return {
            "window_days": window_days,
            "total_executions": total_executions,
            "total_estimated_usd": round(total_usd, 6),
            "per_repo": per_repo,
        }

    def record_proposal_budget_suppression(self, *, reason: str, now: datetime, evidence: dict[str, object]) -> None:
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "proposal_budget_suppressed",
                    "reason": reason,
                    "evidence": evidence,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def record_task_artifact(self, *, task_id: str, artifact: dict[str, Any], now: datetime) -> None:
        """Persist a structured execution artifact keyed by task_id.

        Callers should pass fields like ``outcome_status``, ``changed_files``,
        ``validation_passed``, ``blocked_classification``, and
        ``pull_request_url`` so that future runs and the improve watcher can
        make better-informed decisions without re-reading every comment.
        """
        with self._exclusive():
            data = self.load()
            artifacts = dict(data.get("task_artifacts", {}))
            artifacts[task_id] = {**artifact, "recorded_at": now.isoformat()}
            data["task_artifacts"] = artifacts
            self.save(data, now=now)

    def get_task_artifact(self, task_id: str) -> dict[str, Any] | None:
        """Return the most recent execution artifact for *task_id*, or ``None``."""
        data = self.load()
        artifacts = data.get("task_artifacts", {})
        result = artifacts.get(task_id)
        if result is None:
            return None
        return dict(result) if isinstance(result, dict) else None

    def _append_event(self, data: dict[str, Any], event: dict[str, Any], *, now: datetime) -> None:
        events = list(data.get("events", []))
        events.append(event)
        data["events"] = events
        self.save(data, now=now)

    @staticmethod
    def issue_signature(issue: dict[str, Any]) -> str:
        state = issue.get("state")
        if isinstance(state, dict):
            state_name = str(state.get("name", "")).strip()
        else:
            state_name = str(state or "").strip()
        updated_at = str(issue.get("updated_at") or issue.get("updated") or "").strip()
        # Plane stores descriptions in description_html; description and
        # description_stripped are typically null.  Fall back to description_html
        # so the signature actually varies with task content — otherwise every
        # task has an empty description and a re-queued task with unchanged
        # updated_at produces a false noop match on the next cycle.
        description = (
            str(issue.get("description") or "").strip()
            or str(issue.get("description_stripped") or "").strip()
            or str(issue.get("description_html") or "").strip()
        )
        return "|".join([str(issue.get("id", "")), state_name, updated_at, description])

    @staticmethod
    def _exec_count(events: list[dict[str, Any]], *, since: datetime) -> int:
        count = 0
        for event in events:
            if event.get("kind") != "execution":
                continue
            try:
                ts = datetime.fromisoformat(str(event.get("timestamp")))
            except ValueError:
                continue
            if ts >= since:
                count += 1
        return count

    @staticmethod
    def _prune_events(events: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=7)
        retained: list[dict[str, Any]] = []
        for event in events:
            try:
                ts = datetime.fromisoformat(str(event.get("timestamp")))
            except ValueError:
                continue
            if ts >= cutoff:
                retained.append(event)
        return retained[-1000:]
