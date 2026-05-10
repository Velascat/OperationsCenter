# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Deterministic queue self-healing rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .models import QueueHealingDecision, QueueHealingTask, QueueTransition


class QueueHealingEngine:
    def __init__(
        self,
        *,
        stale_blocked_seconds: int = 3600,
        max_retry_count: int = 2,
        max_recovery_attempts: int = 5,
    ) -> None:
        self.stale_blocked_seconds = stale_blocked_seconds
        self.max_retry_count = max_retry_count
        self.max_recovery_attempts = max_recovery_attempts

    def decide(
        self,
        task: QueueHealingTask,
        *,
        no_consumer_can_execute: bool = False,
        now: datetime | None = None,
    ) -> QueueHealingDecision:
        now = now or datetime.now(UTC)
        state = task.state.strip().lower()
        if state != "blocked":
            return QueueHealingDecision(
                task_id=task.task_id,
                transition=QueueTransition.NONE,
                reason="task is not blocked",
            )
        if task.recovery_attempt_count >= self.max_recovery_attempts:
            return QueueHealingDecision(
                task_id=task.task_id,
                transition=QueueTransition.ESCALATE,
                reason="recovery attempt budget exhausted",
                retry_lineage_id=task.retry_lineage_id,
                escalate=True,
            )
        if task.retry_count >= self.max_retry_count:
            return QueueHealingDecision(
                task_id=task.task_id,
                transition=QueueTransition.ESCALATE,
                reason="retry replay budget exhausted",
                retry_lineage_id=task.retry_lineage_id,
                escalate=True,
            )
        if (
            task.duplicate_exists_in_blocked
            and no_consumer_can_execute
            and task.retry_safe
        ):
            return QueueHealingDecision(
                task_id=task.task_id,
                transition=QueueTransition.BLOCKED_TO_READY_FOR_AI,
                reason="duplicate suppression deadlock is retry-safe",
                retry_lineage_id=task.retry_lineage_id,
                safe=True,
            )
        if self._is_stale(task, now) and task.retry_safe:
            return QueueHealingDecision(
                task_id=task.task_id,
                transition=QueueTransition.BLOCKED_TO_BACKLOG,
                reason="stale blocked task is retry-safe",
                retry_lineage_id=task.retry_lineage_id,
                safe=True,
            )
        return QueueHealingDecision(
            task_id=task.task_id,
            transition=QueueTransition.NONE,
            reason="no safe queue-healing rule matched",
            retry_lineage_id=task.retry_lineage_id,
        )

    def _is_stale(self, task: QueueHealingTask, now: datetime) -> bool:
        if task.updated_at is None:
            return False
        updated = task.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return now - updated >= timedelta(seconds=self.stale_blocked_seconds)
