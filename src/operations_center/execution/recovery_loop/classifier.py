# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ExecutionResult → ExecutionFailureKind classification.

The default classifier uses structured fields on ``ExecutionResult``
(``status``, ``failure_category``, ``failure_reason``) plus an optional
``AdapterErrorCode`` hint that adapters may attach via the ``artifacts``
list. It does not parse free-form log text.

See ``docs/architecture/recovery/recovery_loop_design.md`` for the full design.
"""

from __future__ import annotations

from typing import Protocol

from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionResult

from .models import AdapterErrorCode, ExecutionFailureKind, RecoveryContext


class FailureClassifier(Protocol):
    """Maps an ``ExecutionResult`` into an ``ExecutionFailureKind``."""

    def classify(
        self,
        result: ExecutionResult,
        context: RecoveryContext,
    ) -> ExecutionFailureKind:
        ...


def _adapter_error_code(result: ExecutionResult) -> AdapterErrorCode | None:
    """Best-effort extraction of an ``AdapterErrorCode`` from result metadata.

    Adapters may attach a code via the ``artifacts`` list (the
    ``ExecutionArtifact`` shape includes a ``payload`` field), or via a
    ``failure_reason`` prefix of the form ``"adapter_error_code=<value>:..."``.
    Both are best-effort hints; absence returns ``None``.
    """
    reason = result.failure_reason or ""
    prefix = "adapter_error_code="
    if reason.startswith(prefix):
        token = reason[len(prefix):].split(":", 1)[0].strip().lower()
        try:
            return AdapterErrorCode(token)
        except ValueError:
            return None
    return None


class DefaultFailureClassifier:
    """Conservative default classifier.

    Mapping rules (in order):

    1. ``result.success`` is True → ``NONE``
    2. ``result.status == TIMED_OUT`` or ``failure_category == TIMEOUT`` → ``TIMEOUT``
    3. ``failure_category == POLICY_BLOCKED`` → ``CONFIGURATION``
    4. ``failure_category == VALIDATION_FAILED`` → ``CONTRACT_VIOLATION``
    5. ``failure_category == ROUTING_ERROR`` → ``CONFIGURATION``
    6. Adapter error code (if present and recognized):
         * ``RATE_LIMIT`` → ``RATE_LIMIT``
         * ``BACKEND_UNAVAILABLE`` → ``BACKEND_UNAVAILABLE``
         * ``AUTH_FAILED`` → ``AUTH``
         * ``CONTRACT_REJECTED`` → ``CONTRACT_VIOLATION``
         * ``EXECUTOR_ERROR`` → ``TRANSIENT``
         * ``TIMEOUT`` → ``TIMEOUT``
    7. ``failure_category == BACKEND_ERROR`` (without adapter code) → ``UNKNOWN``
    8. Otherwise → ``UNKNOWN``
    """

    def classify(
        self,
        result: ExecutionResult,
        context: RecoveryContext,  # noqa: ARG002 — accept for protocol parity
    ) -> ExecutionFailureKind:
        if result.success:
            return ExecutionFailureKind.NONE

        if (
            result.status == ExecutionStatus.TIMED_OUT
            or result.failure_category == FailureReasonCategory.TIMEOUT
        ):
            return ExecutionFailureKind.TIMEOUT

        if result.failure_category == FailureReasonCategory.POLICY_BLOCKED:
            return ExecutionFailureKind.CONFIGURATION

        if result.failure_category == FailureReasonCategory.VALIDATION_FAILED:
            return ExecutionFailureKind.CONTRACT_VIOLATION

        if result.failure_category == FailureReasonCategory.ROUTING_ERROR:
            return ExecutionFailureKind.CONFIGURATION

        code = _adapter_error_code(result)
        if code is not None:
            mapping = {
                AdapterErrorCode.RATE_LIMIT: ExecutionFailureKind.RATE_LIMIT,
                AdapterErrorCode.BACKEND_UNAVAILABLE: ExecutionFailureKind.BACKEND_UNAVAILABLE,
                AdapterErrorCode.AUTH_FAILED: ExecutionFailureKind.AUTH,
                AdapterErrorCode.CONTRACT_REJECTED: ExecutionFailureKind.CONTRACT_VIOLATION,
                AdapterErrorCode.EXECUTOR_ERROR: ExecutionFailureKind.TRANSIENT,
                AdapterErrorCode.TIMEOUT: ExecutionFailureKind.TIMEOUT,
            }
            mapped = mapping.get(code)
            if mapped is not None:
                return mapped

        return ExecutionFailureKind.UNKNOWN


__all__ = ["DefaultFailureClassifier", "FailureClassifier"]
