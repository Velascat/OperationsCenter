# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Queue task metadata needed for safe self-healing decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class QueueTransition(str, Enum):
    NONE = "none"
    BLOCKED_TO_BACKLOG = "blocked_to_backlog"
    BLOCKED_TO_READY_FOR_AI = "blocked_to_ready_for_ai"
    STALE_LOCK_CLEANUP = "stale_lock_cleanup"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class QueueHealingTask:
    task_id: str
    title: str
    state: str
    duplicate_key: str | None = None
    duplicate_exists_in_blocked: bool = False
    retry_safe: bool = False
    blocked_reason: str | None = None
    blocked_by_backend: str | None = None
    backend_dependency: str | None = None
    retry_lineage_id: str | None = None
    retry_count: int = 0
    recovery_attempt_count: int = 0
    updated_at: datetime | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class QueueHealingDecision:
    task_id: str
    transition: QueueTransition
    reason: str
    retry_lineage_id: str | None = None
    safe: bool = False
    escalate: bool = False
