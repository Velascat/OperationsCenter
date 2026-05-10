# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Recovery-oriented telemetry emitted by watchers and runtime services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class RecoveryTelemetryEvent:
    event: str
    watcher: str
    task_id: str | None = None
    backend: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WatcherRecoveryTelemetry:
    watcher: str
    executor_exit_code: int | None = None
    executor_signal: str | None = None
    retry_strategy_used: str | None = None
    retry_strategy_changed: bool | None = None
    remediation_attempt_number: int | None = None
    remediation_lineage_id: str | None = None
    prior_failure_signature: str | None = None
    blocked_reason: str | None = None
    blocked_by_backend: str | None = None
    retry_safe: bool | None = None
    queue_transition_recommendation: str | None = None
    duplicate_reason: str | None = None
    suppression_reason: str | None = None
    starvation_detected: bool | None = None
    queue_deadlock_detected: bool | None = None
    backend_health_transition: str | None = None
    cooldown_applied: bool | None = None
    recovery_attempt_started: bool | None = None
    recovery_attempt_result: str | None = None
