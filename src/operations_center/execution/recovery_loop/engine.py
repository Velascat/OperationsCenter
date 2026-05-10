# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""RecoveryEngine — owns classification + handler selection.

Evaluation rules (in order):

1. Successful result → ACCEPT
2. Classify the failure
3. UNKNOWN → REJECT (unless ``policy.retry_unknowns`` enabled)
4. Attempt budget exhausted → STOP_ATTEMPT_BUDGET_EXHAUSTED
5. Non-retryable kind → REJECT_UNRECOVERABLE
6. Non-idempotent request and failure not pre-send → STOP_IDEMPOTENCY_REQUIRED
7. Retry budget checker refuses → STOP_COST_BUDGET_EXHAUSTED
8. RATE_LIMIT without usable backoff → STOP_BACKOFF_REQUIRED
9. RATE_LIMIT with usable backoff → return outcome with bounded delay
10. First handler returning a non-None outcome wins
11. No handler matched → REJECT_UNRECOVERABLE
12. Clamp ``outcome.delay_seconds`` if present
13. Return outcome
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from operations_center.contracts.execution import ExecutionResult
from operations_center.backend_health import BackendHealthRegistry, BackendHealthState

from .classifier import FailureClassifier
from .handlers import RecoveryHandler
from .models import (
    AdapterErrorCode,
    ExecutionFailureKind,
    RecoveryAction,
    RecoveryContext,
    RecoveryDecision,
    RecoveryOutcome,
)
from .policy import RecoveryPolicy, RetryBudgetChecker


_RETRY_AFTER_KEYS = ("retry_after", "retry_after_seconds")


def _retry_after_seconds(result: ExecutionResult) -> float | None:
    """Extract a usable ``retry_after`` value (seconds) from result hints.

    The default ``ExecutionResult`` doesn't have a structured
    ``error_details`` map; we treat the ``failure_reason`` field as a
    last-resort carrier. Adapters that want bounded retry-after support
    must include it in the form ``"... retry_after=<seconds> ..."``.

    Returns ``None`` if no usable value found.
    """
    reason = result.failure_reason or ""
    for key in _RETRY_AFTER_KEYS:
        token = f"{key}="
        idx = reason.find(token)
        if idx == -1:
            continue
        rest = reason[idx + len(token):]
        end = 0
        while end < len(rest) and (rest[end].isdigit() or rest[end] == "."):
            end += 1
        if end == 0:
            continue
        try:
            value = float(rest[:end])
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _build_action(
    *,
    attempt: int,
    failure_kind: ExecutionFailureKind,
    decision: RecoveryDecision,
    reason: str,
    handler_name: str | None = None,
) -> RecoveryAction:
    return RecoveryAction(
        attempt=attempt,
        failure_kind=failure_kind,
        decision=decision,
        reason=reason,
        handler_name=handler_name,
    )


class RecoveryEngine:
    """Recovery engine that owns classification + handler selection."""

    def __init__(
        self,
        *,
        classifier: FailureClassifier,
        policy: RecoveryPolicy,
        handlers: Sequence[RecoveryHandler],
        budget_checker: RetryBudgetChecker | None = None,
        backend_health_registry: BackendHealthRegistry | None = None,
    ) -> None:
        self._classifier = classifier
        self._policy = policy
        self._handlers = tuple(handlers)
        self._budget_checker = budget_checker
        self._backend_health_registry = backend_health_registry

    def evaluate(
        self,
        result: ExecutionResult,
        context: RecoveryContext,
    ) -> RecoveryOutcome:
        # Rule 1: success
        if result.success:
            return RecoveryOutcome(
                decision=RecoveryDecision.ACCEPT,
                action=_build_action(
                    attempt=context.attempt,
                    failure_kind=ExecutionFailureKind.NONE,
                    decision=RecoveryDecision.ACCEPT,
                    reason="result.success is True",
                ),
            )

        # Rule 2: classify
        failure_kind = self._classifier.classify(result, context)

        backend_id = _backend_id(context)
        if self._backend_health_registry is not None and backend_id is not None:
            _record, transition = self._backend_health_registry.record_failure(
                backend_id,
                result,
                now=datetime.now(UTC),
            )
            if (
                transition.current
                in {BackendHealthState.UNSTABLE, BackendHealthState.UNAVAILABLE}
                and transition.cooldown_applied
            ):
                return RecoveryOutcome(
                    decision=RecoveryDecision.STOP_COOLDOWN_REQUIRED,
                    action=_build_action(
                        attempt=context.attempt,
                        failure_kind=failure_kind,
                        decision=RecoveryDecision.STOP_COOLDOWN_REQUIRED,
                        reason=(
                            "backend health registry applied cooldown after "
                            f"{transition.reason}"
                        ),
                        handler_name="backend_health_registry",
                    ),
                )

        # Rule 3: UNKNOWN reject (unless policy opts in)
        if failure_kind == ExecutionFailureKind.UNKNOWN:
            if not self._policy.retry_unknowns:
                return RecoveryOutcome(
                    decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                    action=_build_action(
                        attempt=context.attempt,
                        failure_kind=failure_kind,
                        decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                        reason="UNKNOWN failure with retry_unknowns disabled",
                    ),
                )
            # Opt-in unknown retry: respect the separate budget
            unknown_retries_used = sum(
                1 for a in context.previous_actions
                if a.failure_kind == ExecutionFailureKind.UNKNOWN
            )
            if unknown_retries_used >= self._policy.unknown_retry_limit:
                return RecoveryOutcome(
                    decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                    action=_build_action(
                        attempt=context.attempt,
                        failure_kind=failure_kind,
                        decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                        reason=(
                            f"UNKNOWN retry limit reached "
                            f"({unknown_retries_used}/{self._policy.unknown_retry_limit})"
                        ),
                    ),
                )

        # Rule 4: attempt budget
        if context.attempt >= self._policy.max_attempts:
            return RecoveryOutcome(
                decision=RecoveryDecision.STOP_ATTEMPT_BUDGET_EXHAUSTED,
                action=_build_action(
                    attempt=context.attempt,
                    failure_kind=failure_kind,
                    decision=RecoveryDecision.STOP_ATTEMPT_BUDGET_EXHAUSTED,
                    reason=(
                        f"attempt {context.attempt} reached max_attempts="
                        f"{self._policy.max_attempts}"
                    ),
                ),
            )

        # Rule 5: explicit non-retryable
        if failure_kind in self._policy.non_retryable_kinds:
            return RecoveryOutcome(
                decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                action=_build_action(
                    attempt=context.attempt,
                    failure_kind=failure_kind,
                    decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                    reason=f"failure kind {failure_kind.value} is non-retryable per policy",
                ),
            )

        # Rule 6: idempotency
        is_pre_send = failure_kind in self._policy.pre_send_failure_kinds
        if not context.current_request.idempotent and not is_pre_send:
            return RecoveryOutcome(
                decision=RecoveryDecision.STOP_IDEMPOTENCY_REQUIRED,
                action=_build_action(
                    attempt=context.attempt,
                    failure_kind=failure_kind,
                    decision=RecoveryDecision.STOP_IDEMPOTENCY_REQUIRED,
                    reason=(
                        f"non-idempotent request and failure {failure_kind.value} "
                        "is not in pre_send_failure_kinds"
                    ),
                ),
            )

        # Rule 7: cost budget
        if (
            self._budget_checker is not None
            and not self._budget_checker.can_retry(context.current_request, context)
        ):
            return RecoveryOutcome(
                decision=RecoveryDecision.STOP_COST_BUDGET_EXHAUSTED,
                action=_build_action(
                    attempt=context.attempt,
                    failure_kind=failure_kind,
                    decision=RecoveryDecision.STOP_COST_BUDGET_EXHAUSTED,
                    reason=(
                        f"retry budget checker {type(self._budget_checker).__name__} "
                        "refused retry"
                    ),
                ),
            )

        # Rules 8+9: rate-limit backoff handling
        delay_seconds: float | None = None
        if failure_kind == ExecutionFailureKind.RATE_LIMIT:
            if not self._policy.rate_limit_retry_requires_backoff:
                pass  # allow immediate retry — caller-explicit risk
            else:
                ra = _retry_after_seconds(result)
                if ra is None:
                    return RecoveryOutcome(
                        decision=RecoveryDecision.STOP_BACKOFF_REQUIRED,
                        action=_build_action(
                            attempt=context.attempt,
                            failure_kind=failure_kind,
                            decision=RecoveryDecision.STOP_BACKOFF_REQUIRED,
                            reason="RATE_LIMIT without usable retry_after metadata",
                        ),
                    )
                if ra > self._policy.max_delay_seconds:
                    return RecoveryOutcome(
                        decision=RecoveryDecision.STOP_BACKOFF_REQUIRED,
                        action=_build_action(
                            attempt=context.attempt,
                            failure_kind=failure_kind,
                            decision=RecoveryDecision.STOP_BACKOFF_REQUIRED,
                            reason=(
                                f"RATE_LIMIT retry_after={ra}s exceeds "
                                f"max_delay_seconds={self._policy.max_delay_seconds}s"
                            ),
                        ),
                    )
                delay_seconds = ra

        # Rules 10+11: pick a handler
        for handler in self._handlers:
            outcome = handler.recover(failure_kind, result, context)
            if outcome is None:
                continue
            # Rule 12: clamp delay if engine derived one above
            final_delay = (
                outcome.delay_seconds
                if outcome.delay_seconds is not None
                else delay_seconds
            )
            if final_delay is not None:
                final_delay = min(final_delay, self._policy.max_delay_seconds)
            return RecoveryOutcome(
                decision=outcome.decision,
                action=outcome.action,
                next_request=outcome.next_request,
                requires_policy_revalidation=outcome.requires_policy_revalidation,
                delay_seconds=final_delay,
            )

        # Rule 11 (fallback): no handler matched
        return RecoveryOutcome(
            decision=RecoveryDecision.REJECT_UNRECOVERABLE,
            action=_build_action(
                attempt=context.attempt,
                failure_kind=failure_kind,
                decision=RecoveryDecision.REJECT_UNRECOVERABLE,
                reason="no handler returned an outcome",
            ),
        )


def _backend_id(context: RecoveryContext) -> str | None:
    target = context.current_request.bound_target
    if target is not None and target.backend:
        return target.backend
    binding = context.current_request.runtime_binding
    if binding is not None and binding.kind:
        return binding.kind
    return None


# Adapter error code is unused here directly; re-exported from models.py via __init__.
_ = AdapterErrorCode  # silence unused-import warnings during refactors


__all__ = ["RecoveryEngine"]
