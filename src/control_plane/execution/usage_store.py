from __future__ import annotations

import json
import os
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Generator, cast

from control_plane.execution.models import BudgetDecision, ExecutionControlSettings, NoOpDecision, RetryDecision

# ---------------------------------------------------------------------------
# Module-level path-keyed threading locks.
# All UsageStore instances that share the same on-disk path share a lock so
# that load-modify-save triples are atomic even when parallel_slots > 1.
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
_CB_THRESHOLD = float(os.environ.get("CONTROL_PLANE_CIRCUIT_BREAKER_THRESHOLD", "0.8"))
_CB_WINDOW = max(3, int(os.environ.get("CONTROL_PLANE_CIRCUIT_BREAKER_WINDOW", "5")))


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
        return json.loads(self.path.read_text())

    def save(self, data: dict[str, Any], *, now: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
        tmp.write_text(json.dumps(data, indent=2))
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
        # Circuit breaker: if ≥ threshold fraction of last _CB_WINDOW execution
        # outcomes failed, pause execution until the operator investigates.
        # Requires at least 3 samples to avoid false positives at startup.
        outcomes = [
            e for e in reversed(events)
            if e.get("kind") == "execution_outcome"
        ][:_CB_WINDOW]
        if len(outcomes) >= 3:
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
            ref = now or datetime.now(last_attempt_ts.tzinfo if last_attempt_ts else None)
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
        assert isinstance(events, list)
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
        self.path.write_text(json.dumps(data, indent=2))

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

    def record_execution(self, *, role: str, task_id: str, signature: str, now: datetime) -> None:
        with self._exclusive():
            data = self.load()
            attempts = dict(data.get("task_attempts", {}))
            attempts[task_id] = int(attempts.get(task_id, 0)) + 1
            data["task_attempts"] = attempts
            signatures = dict(data.get("last_task_signatures", {}))
            signatures[f"{role}:{task_id}"] = signature
            data["last_task_signatures"] = signatures
            self._append_event(
                data,
                {
                    "kind": "execution",
                    "role": role,
                    "task_id": task_id,
                    "signature": signature,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

    def record_execution_outcome(
        self,
        *,
        task_id: str,
        role: str,
        succeeded: bool,
        now: datetime,
    ) -> None:
        """Record whether a goal/test execution produced a successful result.

        These events feed the circuit breaker in ``budget_decision``.  Record
        after the task handler has determined the final outcome — not before.
        """
        with self._exclusive():
            data = self.load()
            self._append_event(
                data,
                {
                    "kind": "execution_outcome",
                    "task_id": task_id,
                    "role": role,
                    "succeeded": succeeded,
                    "timestamp": now.isoformat(),
                },
                now=now,
            )

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
        now = now or datetime.now()
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
        now = now or datetime.now()
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
        _now = now or datetime.now()
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
        description = str(issue.get("description") or issue.get("description_stripped") or "").strip()
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
