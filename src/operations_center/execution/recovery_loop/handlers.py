# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Recovery handlers — strategy objects for recovery decisions.

The handler protocol uses a single ``recover()`` method. Returning ``None``
means "this handler does not apply"; the engine then tries the next handler.
"""

from __future__ import annotations

from typing import Protocol

from operations_center.contracts.execution import ExecutionResult

from .models import (
    ExecutionFailureKind,
    RecoveryAction,
    RecoveryContext,
    RecoveryDecision,
    RecoveryOutcome,
)


class RecoveryHandler(Protocol):
    """Strategy object that may produce a ``RecoveryOutcome`` for a failure.

    ``None`` means the handler does not apply.
    """

    name: str

    def recover(
        self,
        failure_kind: ExecutionFailureKind,
        result: ExecutionResult,
        context: RecoveryContext,
    ) -> RecoveryOutcome | None:
        ...


class RetrySameRequestHandler:
    """Returns ``RETRY_SAME_REQUEST`` for retryable kinds.

    The handler trusts that the engine has already verified attempt budget,
    cost budget, idempotency, and rate-limit-backoff requirements before
    selecting it.
    """

    name: str = "retry_same_request"

    def __init__(self, kinds: frozenset[ExecutionFailureKind]) -> None:
        self._kinds = kinds

    def recover(
        self,
        failure_kind: ExecutionFailureKind,
        result: ExecutionResult,  # noqa: ARG002
        context: RecoveryContext,
    ) -> RecoveryOutcome | None:
        if failure_kind not in self._kinds:
            return None
        action = RecoveryAction(
            attempt=context.attempt,
            failure_kind=failure_kind,
            decision=RecoveryDecision.RETRY_SAME_REQUEST,
            reason=f"retryable failure kind: {failure_kind.value}",
            handler_name=self.name,
        )
        return RecoveryOutcome(
            decision=RecoveryDecision.RETRY_SAME_REQUEST,
            action=action,
            next_request=context.current_request,
        )


class RejectUnrecoverableHandler:
    """Returns ``REJECT_UNRECOVERABLE`` for non-retryable kinds.

    Default coverage: AUTH, CONFIGURATION, CONTRACT_VIOLATION, UNKNOWN.
    """

    name: str = "reject_unrecoverable"

    def __init__(self, kinds: frozenset[ExecutionFailureKind]) -> None:
        self._kinds = kinds

    def recover(
        self,
        failure_kind: ExecutionFailureKind,
        result: ExecutionResult,  # noqa: ARG002
        context: RecoveryContext,
    ) -> RecoveryOutcome | None:
        if failure_kind not in self._kinds:
            return None
        action = RecoveryAction(
            attempt=context.attempt,
            failure_kind=failure_kind,
            decision=RecoveryDecision.REJECT_UNRECOVERABLE,
            reason=f"non-retryable failure kind: {failure_kind.value}",
            handler_name=self.name,
        )
        return RecoveryOutcome(
            decision=RecoveryDecision.REJECT_UNRECOVERABLE,
            action=action,
        )


__all__ = [
    "RecoveryHandler",
    "RejectUnrecoverableHandler",
    "RetrySameRequestHandler",
]
