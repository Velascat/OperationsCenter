# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Bounded recovery loop wrapping ``ExecutionCoordinator``'s adapter call.

See ``docs/architecture/recovery/recovery_loop_design.md``.
"""

from .classifier import DefaultFailureClassifier, FailureClassifier
from .engine import RecoveryEngine
from .handlers import (
    RecoveryHandler,
    RejectUnrecoverableHandler,
    RetrySameRequestHandler,
)
from .models import (
    AdapterErrorCode,
    ExecutionFailureKind,
    RecoveryAction,
    RecoveryContext,
    RecoveryDecision,
    RecoveryMetadata,
    RecoveryOutcome,
)
from .policy import (
    NoPaidRetryBudgetChecker,
    RecoveryPolicy,
    RetryBudgetChecker,
    UnboundedRetryBudgetChecker,
)
from .timing import bounded_sleep


def metadata_to_summary(metadata: RecoveryMetadata):
    """Convert a recovery_loop RecoveryMetadata dataclass into the
    contracts-layer Pydantic mirror that ``ExecutionResult.recovery`` uses.

    The contracts layer cannot import recovery_loop without creating a
    cycle, so this conversion lives here.
    """
    from operations_center.contracts.execution import (
        RecoveryActionSummary,
        RecoveryMetadataSummary,
    )

    return RecoveryMetadataSummary(
        attempts=metadata.attempts,
        actions=[
            RecoveryActionSummary(
                attempt=a.attempt,
                failure_kind=a.failure_kind.value,
                decision=a.decision.value,
                reason=a.reason,
                handler_name=a.handler_name,
                modified_fields=list(a.modified_fields),
                delay_seconds=a.delay_seconds,
                executor_exit_code=a.executor_exit_code,
                executor_signal=a.executor_signal,
                retry_strategy_used=a.retry_strategy_used,
                retry_strategy_changed=a.retry_strategy_changed,
                remediation_attempt_number=a.remediation_attempt_number,
                remediation_lineage_id=a.remediation_lineage_id,
                prior_failure_signature=a.prior_failure_signature,
            )
            for a in metadata.actions
        ],
        final_decision=metadata.final_decision.value,
        retry_refused_reason=metadata.retry_refused_reason,
    )


def attach_recovery_metadata(result, actions: tuple[RecoveryAction, ...]):
    """Return a new ``ExecutionResult`` with ``recovery`` populated from actions.

    Reads the final decision from the last action and computes a coherent
    ``retry_refused_reason`` when applicable.
    """
    if not actions:
        return result

    last = actions[-1]
    final_decision = last.decision

    refused_reasons = {
        RecoveryDecision.STOP_ATTEMPT_BUDGET_EXHAUSTED: "attempt budget exhausted",
        RecoveryDecision.STOP_COST_BUDGET_EXHAUSTED: "cost budget exhausted",
        RecoveryDecision.STOP_IDEMPOTENCY_REQUIRED: "non-idempotent request, retry refused",
        RecoveryDecision.STOP_BACKOFF_REQUIRED: "rate-limit retry requires bounded backoff",
        RecoveryDecision.STOP_COOLDOWN_REQUIRED: "backend cooldown active",
        RecoveryDecision.REJECT_UNRECOVERABLE: "non-retryable failure",
    }
    retry_refused_reason = refused_reasons.get(final_decision)

    metadata = RecoveryMetadata(
        attempts=last.attempt,
        actions=tuple(actions),
        final_decision=final_decision,
        retry_refused_reason=retry_refused_reason,
    )
    summary = metadata_to_summary(metadata)
    return result.model_copy(update={"recovery": summary})


def build_default_engine(policy: RecoveryPolicy | None = None) -> RecoveryEngine:
    """Construct a RecoveryEngine wired with conservative defaults.

    Suitable for ``ExecutionCoordinator`` when no caller-supplied recovery
    config is available.
    """
    p = policy or RecoveryPolicy()
    handlers = [
        RetrySameRequestHandler(p.retryable_kinds),
        RejectUnrecoverableHandler(p.non_retryable_kinds),
    ]
    return RecoveryEngine(
        classifier=DefaultFailureClassifier(),
        policy=p,
        handlers=handlers,
        budget_checker=None,
    )


__all__ = [
    "AdapterErrorCode",
    "DefaultFailureClassifier",
    "ExecutionFailureKind",
    "FailureClassifier",
    "NoPaidRetryBudgetChecker",
    "RecoveryAction",
    "RecoveryContext",
    "RecoveryDecision",
    "RecoveryEngine",
    "RecoveryHandler",
    "RecoveryMetadata",
    "RecoveryOutcome",
    "RecoveryPolicy",
    "RejectUnrecoverableHandler",
    "RetryBudgetChecker",
    "RetrySameRequestHandler",
    "UnboundedRetryBudgetChecker",
    "attach_recovery_metadata",
    "bounded_sleep",
    "build_default_engine",
    "metadata_to_summary",
]
