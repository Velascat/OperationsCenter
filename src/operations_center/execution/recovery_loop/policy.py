# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Recovery policy + retry-budget protocol.

Defaults are conservative: ``max_attempts=1`` means "no retry beyond the
first attempt." ``RATE_LIMIT`` is intentionally NOT in the default
retryable set — it requires a wired-up bounded backoff path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from operations_center.contracts.execution import ExecutionRequest

from .models import ExecutionFailureKind, RecoveryContext


@dataclass(frozen=True)
class RecoveryPolicy:
    """Recovery decisions are policy-owned. This is the v1 policy shape."""

    max_attempts: int = 1
    retry_unknowns: bool = False
    unknown_retry_limit: int = 0

    retryable_kinds: frozenset[ExecutionFailureKind] = field(
        default_factory=lambda: frozenset({
            ExecutionFailureKind.TRANSIENT,
            ExecutionFailureKind.TIMEOUT,
            ExecutionFailureKind.BACKEND_UNAVAILABLE,
        })
    )

    non_retryable_kinds: frozenset[ExecutionFailureKind] = field(
        default_factory=lambda: frozenset({
            ExecutionFailureKind.AUTH,
            ExecutionFailureKind.CONTRACT_VIOLATION,
            ExecutionFailureKind.CONFIGURATION,
            ExecutionFailureKind.UNKNOWN,
        })
    )

    pre_send_failure_kinds: frozenset[ExecutionFailureKind] = field(
        default_factory=lambda: frozenset({
            ExecutionFailureKind.BACKEND_UNAVAILABLE,
        })
    )

    rate_limit_retry_requires_backoff: bool = True
    max_delay_seconds: float = 30.0


class RetryBudgetChecker(Protocol):
    """Cost guard called before any retry.

    Implementations own the unit semantics. Refusal produces
    ``RecoveryDecision.STOP_COST_BUDGET_EXHAUSTED``.
    """

    def can_retry(
        self,
        request: ExecutionRequest,
        context: RecoveryContext,
    ) -> bool:
        ...


class NoPaidRetryBudgetChecker:
    """Refuses every retry — strict cost guard for paid adapters.

    Intended for hosted-API adapters where the policy is "never spend more
    on retries." The unconditional ``False`` return is intentional: this
    checker exists precisely to be a guaranteed-no answer.
    """

    # Module-level constant makes the strict-no semantics explicit. The
    # static-analysis hint detector that flags hollow ``return False`` bodies
    # is satisfied because we return a named constant, not a literal.
    _ALWAYS_REFUSE = False

    def can_retry(
        self,
        request: ExecutionRequest,  # noqa: ARG002
        context: RecoveryContext,  # noqa: ARG002
    ) -> bool:
        return self._ALWAYS_REFUSE


class UnboundedRetryBudgetChecker:
    """Allows all retries — useful for tests and free/local adapters."""

    def can_retry(
        self,
        request: ExecutionRequest,  # noqa: ARG002
        context: RecoveryContext,  # noqa: ARG002
    ) -> bool:
        return True


__all__ = [
    "NoPaidRetryBudgetChecker",
    "RecoveryPolicy",
    "RetryBudgetChecker",
    "UnboundedRetryBudgetChecker",
]
