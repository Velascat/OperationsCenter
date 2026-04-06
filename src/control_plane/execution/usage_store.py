from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from control_plane.execution.models import BudgetDecision, ExecutionControlSettings, NoOpDecision, RetryDecision


class UsageStore:
    def __init__(self, path: Path | None = None) -> None:
        self.settings = ExecutionControlSettings.from_env()
        self.path = path or self.settings.usage_path

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
        self.path.write_text(json.dumps(data, indent=2))

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

    def record_proposal_budget_suppression(self, *, reason: str, now: datetime, evidence: dict[str, object]) -> None:
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
