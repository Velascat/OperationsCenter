# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""First-class runtime backend health state.

The registry is intentionally small and deterministic. It records facts the
watchdog loop used to infer repeatedly from logs: failure counters, cooldowns,
last success/failure signatures, and the bounded strategy currently allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum


class BackendHealthState(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNSTABLE = "unstable"
    UNAVAILABLE = "unavailable"
    RECOVERING = "recovering"
    OPERATOR_BLOCKED = "operator_blocked"


class RecoveryStrategy(str, Enum):
    NONE = "none"
    RETRY_AFTER_COOLDOWN = "retry_after_cooldown"
    RESTART_BACKEND = "restart_backend"
    RESTART_WATCHER = "restart_watcher"
    REINITIALIZE_RUNTIME = "reinitialize_runtime"
    REDUCE_PRESSURE = "reduce_pressure"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class BackendFailure:
    signature: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    exit_code: int | None = None
    signal: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BackendHealthRecord:
    backend_id: str
    state: BackendHealthState = BackendHealthState.UNKNOWN
    failure_count: int = 0
    recovery_attempt_count: int = 0
    last_success_at: datetime | None = None
    last_failure: BackendFailure | None = None
    cooldown_until: datetime | None = None
    safe_retry_after: datetime | None = None
    recovery_strategy: RecoveryStrategy = RecoveryStrategy.NONE
    operator_blocked_reason: str | None = None

    def with_update(self, **changes: object) -> "BackendHealthRecord":
        return replace(self, **changes)
