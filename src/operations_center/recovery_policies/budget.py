# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Machine-enforced recovery budget accounting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryBudget:
    max_cycles_before_escalation: int = 3
    max_equivalent_retries: int = 2
    max_recovery_attempts: int = 5


@dataclass(frozen=True)
class RecoveryBudgetDecision:
    allowed: bool
    reason: str
    escalate: bool = False


class RecoveryBudgetTracker:
    """Counts repeated cycles/retries for a single root-cause lineage."""

    def __init__(self, budget: RecoveryBudget | None = None) -> None:
        self.budget = budget or RecoveryBudget()
        self.cycles = 0
        self.equivalent_retries = 0
        self.recovery_attempts = 0

    def record_cycle(self, *, evidence_changed: bool) -> RecoveryBudgetDecision:
        self.cycles = 0 if evidence_changed else self.cycles + 1
        if self.cycles > self.budget.max_cycles_before_escalation:
            return RecoveryBudgetDecision(
                allowed=False,
                reason="max unchanged recovery cycles exceeded",
                escalate=True,
            )
        return RecoveryBudgetDecision(allowed=True, reason="cycle budget available")

    def record_retry(self, *, equivalent: bool) -> RecoveryBudgetDecision:
        self.equivalent_retries = self.equivalent_retries + 1 if equivalent else 0
        if self.equivalent_retries > self.budget.max_equivalent_retries:
            return RecoveryBudgetDecision(
                allowed=False,
                reason="max equivalent retries exceeded",
                escalate=True,
            )
        return RecoveryBudgetDecision(allowed=True, reason="retry budget available")

    def record_recovery_attempt(self) -> RecoveryBudgetDecision:
        self.recovery_attempts += 1
        if self.recovery_attempts > self.budget.max_recovery_attempts:
            return RecoveryBudgetDecision(
                allowed=False,
                reason="max recovery attempts exceeded",
                escalate=True,
            )
        return RecoveryBudgetDecision(allowed=True, reason="recovery attempt budget available")
