# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Recovery loop data model.

Backend-neutral types used by the recovery loop layer that wraps adapter
execution inside ``ExecutionCoordinator``. See
``docs/architecture/recovery/recovery_loop_design.md`` for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from operations_center.contracts.execution import ExecutionRequest


class ExecutionFailureKind(str, Enum):
    """Recovery-policy classification of an ``ExecutionResult``.

    Generic and backend-neutral. Backend-specific mapping lives in classifier
    implementations, never as enum values here.

    ``UNKNOWN`` means: classifier could not map the result into a
    recovery-policy category. (Distinct from ``AdapterErrorCode.UNKNOWN``,
    which means: the adapter could not categorize its own failure.)
    """

    NONE = "none"
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    CONFIGURATION = "configuration"
    CONTRACT_VIOLATION = "contract_violation"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    UNKNOWN = "unknown"


class AdapterErrorCode(str, Enum):
    """Controlled classifier-hint enum for adapter-side error reporting.

    Adapters that need to convey a structured error category should set this
    on result metadata rather than inventing free-form strings.

    Adapter-side contract:

      ``BACKEND_UNAVAILABLE`` means the adapter is **certain** the request
      did not begin processing on the backend. Adapters MUST NOT emit
      ``BACKEND_UNAVAILABLE`` for failures occurring after the request
      body/payload was sent or after backend execution may have started.
      Use ``EXECUTOR_ERROR`` or ``UNKNOWN`` for ambiguous mid-stream
      failures.

    This contract matters because ``BACKEND_UNAVAILABLE`` may be treated as
    a pre-send/no-side-effect failure kind for non-idempotent retry
    decisions.
    """

    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH_FAILED = "auth_failed"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    CONTRACT_REJECTED = "contract_rejected"
    EXECUTOR_ERROR = "executor_error"
    UNKNOWN = "unknown"


class RecoveryDecision(str, Enum):
    """The recovery engine's verdict for a single attempt's result."""

    ACCEPT = "accept"
    RETRY_SAME_REQUEST = "retry_same_request"
    RETRY_MODIFIED_REQUEST = "retry_modified_request"
    REJECT_UNRECOVERABLE = "reject_unrecoverable"
    STOP_ATTEMPT_BUDGET_EXHAUSTED = "stop_attempt_budget_exhausted"
    STOP_COST_BUDGET_EXHAUSTED = "stop_cost_budget_exhausted"
    STOP_IDEMPOTENCY_REQUIRED = "stop_idempotency_required"
    STOP_BACKOFF_REQUIRED = "stop_backoff_required"
    STOP_COOLDOWN_REQUIRED = "stop_cooldown_required"


@dataclass(frozen=True)
class RecoveryAction:
    """One recorded recovery decision for a single attempt.

    ``delay_seconds`` records the bounded sleep duration **actually applied**
    (after clamping by ``policy.max_delay_seconds``), not the unclamped value
    requested. Read None when no delay was enforced.
    """

    attempt: int
    failure_kind: ExecutionFailureKind
    decision: RecoveryDecision
    reason: str
    handler_name: str | None = None
    modified_fields: tuple[str, ...] = ()
    delay_seconds: float | None = None
    executor_exit_code: int | None = None
    executor_signal: str | None = None
    retry_strategy_used: str | None = None
    retry_strategy_changed: bool | None = None
    remediation_attempt_number: int | None = None
    remediation_lineage_id: str | None = None
    prior_failure_signature: str | None = None


@dataclass(frozen=True)
class RecoveryContext:
    """Read-only state passed to classifiers and handlers.

    ``previous_actions`` is ordered oldest-first.
    """

    original_request: ExecutionRequest
    current_request: ExecutionRequest
    attempt: int
    previous_actions: tuple[RecoveryAction, ...]


@dataclass(frozen=True)
class RecoveryOutcome:
    """The recovery engine's decision plus the next request to execute (if any).

    ``delay_seconds`` is **not decorative**. If set, the coordinator must
    enforce it via ``bounded_sleep`` before the next attempt.

    Invariants:

    * ``ACCEPT`` / ``REJECT_UNRECOVERABLE`` / all ``STOP_*`` decisions must
      have ``next_request=None``.
    * Retry decisions must include the request to execute next.
    * ``RETRY_MODIFIED_REQUEST`` must set
      ``requires_policy_revalidation=True``.
    * ``delay_seconds``, if present, must be bounded by
      ``policy.max_delay_seconds``.
    """

    decision: RecoveryDecision
    action: RecoveryAction
    next_request: ExecutionRequest | None = None
    requires_policy_revalidation: bool = False
    delay_seconds: float | None = None


@dataclass(frozen=True)
class RecoveryMetadata:
    """Recovery audit trail attached to the final ``ExecutionResult``.

    ``actions`` is ordered oldest-first.
    """

    attempts: int
    actions: tuple[RecoveryAction, ...]
    final_decision: RecoveryDecision
    retry_refused_reason: str | None = None


__all__ = [
    "AdapterErrorCode",
    "ExecutionFailureKind",
    "RecoveryAction",
    "RecoveryContext",
    "RecoveryDecision",
    "RecoveryMetadata",
    "RecoveryOutcome",
]
