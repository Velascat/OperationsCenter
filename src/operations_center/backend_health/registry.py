# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Deterministic backend health transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from operations_center.contracts.execution import ExecutionResult

from .models import (
    BackendFailure,
    BackendHealthRecord,
    BackendHealthState,
    RecoveryStrategy,
)


@dataclass(frozen=True)
class HealthTransition:
    backend_id: str
    previous: BackendHealthState
    current: BackendHealthState
    reason: str
    cooldown_applied: bool = False
    recovery_strategy: RecoveryStrategy = RecoveryStrategy.NONE


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _failure_signature(result: ExecutionResult) -> str:
    reason = (result.failure_reason or "").strip()
    signal = _extract_signal(reason)
    if signal:
        return f"signal:{signal}"
    if result.failure_category:
        return f"category:{result.failure_category.value}"
    if result.status:
        return f"status:{result.status.value}"
    return "failure:unknown"


def _extract_signal(reason: str) -> str | None:
    upper = reason.upper()
    if "SIGKILL" in upper:
        return "SIGKILL"
    if "SIGNAL=9" in upper or "SIGNAL 9" in upper:
        return "SIGKILL"
    if "SIGTERM" in upper:
        return "SIGTERM"
    return None


def _extract_exit_code(reason: str) -> int | None:
    for token in ("exit_code=", "exit="):
        idx = reason.find(token)
        if idx == -1:
            continue
        rest = reason[idx + len(token):]
        digits = ""
        for ch in rest:
            if ch.isdigit() or (ch == "-" and not digits):
                digits += ch
                continue
            break
        if digits:
            try:
                return int(digits)
            except ValueError:
                return None
    return None


class BackendHealthRegistry:
    """In-memory backend health registry.

    Callers may persist ``records`` externally. This class deliberately avoids
    spawning, sleeping, or mutating runtime policy; it only computes auditable
    state transitions.
    """

    def __init__(
        self,
        *,
        cooldown_seconds: int = 1800,
        unstable_failure_threshold: int = 2,
        unavailable_failure_threshold: int = 5,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.unstable_failure_threshold = unstable_failure_threshold
        self.unavailable_failure_threshold = unavailable_failure_threshold
        self.records: dict[str, BackendHealthRecord] = {}

    def get(self, backend_id: str) -> BackendHealthRecord:
        return self.records.get(backend_id, BackendHealthRecord(backend_id=backend_id))

    def record_success(
        self,
        backend_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[BackendHealthRecord, HealthTransition]:
        now = now or _utcnow()
        previous = self.get(backend_id)
        current = previous.with_update(
            state=BackendHealthState.HEALTHY,
            failure_count=0,
            last_success_at=now,
            cooldown_until=None,
            safe_retry_after=None,
            recovery_strategy=RecoveryStrategy.NONE,
            operator_blocked_reason=None,
        )
        self.records[backend_id] = current
        return current, HealthTransition(
            backend_id=backend_id,
            previous=previous.state,
            current=current.state,
            reason="execution_success",
        )

    def record_failure(
        self,
        backend_id: str,
        result: ExecutionResult,
        *,
        now: datetime | None = None,
    ) -> tuple[BackendHealthRecord, HealthTransition]:
        now = now or _utcnow()
        previous = self.get(backend_id)
        signature = _failure_signature(result)
        signal = _extract_signal(result.failure_reason or "")
        failure_count = previous.failure_count + 1
        cooldown_until: datetime | None = previous.cooldown_until
        safe_retry_after: datetime | None = previous.safe_retry_after
        strategy = RecoveryStrategy.RETRY_AFTER_COOLDOWN
        state = BackendHealthState.DEGRADED
        cooldown_applied = False

        if signal == "SIGKILL":
            state = BackendHealthState.UNSTABLE
            cooldown_until = now + timedelta(seconds=self.cooldown_seconds)
            safe_retry_after = cooldown_until
            strategy = RecoveryStrategy.REDUCE_PRESSURE
            cooldown_applied = True
        elif failure_count >= self.unavailable_failure_threshold:
            state = BackendHealthState.UNAVAILABLE
            strategy = RecoveryStrategy.ESCALATE
        elif failure_count >= self.unstable_failure_threshold:
            state = BackendHealthState.UNSTABLE

        failure = BackendFailure(
            signature=signature,
            timestamp=now,
            exit_code=_extract_exit_code(result.failure_reason or ""),
            signal=signal,
            reason=result.failure_reason,
        )
        current = previous.with_update(
            state=state,
            failure_count=failure_count,
            last_failure=failure,
            cooldown_until=cooldown_until,
            safe_retry_after=safe_retry_after,
            recovery_strategy=strategy,
        )
        self.records[backend_id] = current
        return current, HealthTransition(
            backend_id=backend_id,
            previous=previous.state,
            current=current.state,
            reason=signature,
            cooldown_applied=cooldown_applied,
            recovery_strategy=strategy,
        )

    def start_recovery(
        self,
        backend_id: str,
        *,
        strategy: RecoveryStrategy,
    ) -> tuple[BackendHealthRecord, HealthTransition]:
        previous = self.get(backend_id)
        current = previous.with_update(
            state=BackendHealthState.RECOVERING,
            recovery_attempt_count=previous.recovery_attempt_count + 1,
            recovery_strategy=strategy,
        )
        self.records[backend_id] = current
        return current, HealthTransition(
            backend_id=backend_id,
            previous=previous.state,
            current=current.state,
            reason="recovery_attempt_started",
            recovery_strategy=strategy,
        )

    def mark_operator_blocked(
        self,
        backend_id: str,
        reason: str,
    ) -> tuple[BackendHealthRecord, HealthTransition]:
        previous = self.get(backend_id)
        current = previous.with_update(
            state=BackendHealthState.OPERATOR_BLOCKED,
            recovery_strategy=RecoveryStrategy.ESCALATE,
            operator_blocked_reason=reason,
        )
        self.records[backend_id] = current
        return current, HealthTransition(
            backend_id=backend_id,
            previous=previous.state,
            current=current.state,
            reason=reason,
            recovery_strategy=RecoveryStrategy.ESCALATE,
        )
