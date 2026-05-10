# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Phase 6 dispatch error types.

Raise-vs-return policy
----------------------
Raise (caller must handle):
    RepoLockAlreadyHeldError  — concurrent dispatch for the same repo_id.
    AuditDispatchConfigError  — programming or configuration error (bad repo_id,
                                unsupported audit_type, blocked command).

Return as failed ManagedAuditDispatchResult (operational failures):
    All process errors, timeout, and contract discovery failures are returned
    as structured results with an appropriate FailureKind rather than raised.
    This lets callers inspect partial discovery results (e.g., the process
    failed but run_status.json was still written).
"""

from __future__ import annotations


class AuditDispatchError(Exception):
    """Base for all Phase 6 audit dispatch errors."""


class RepoLockAlreadyHeldError(AuditDispatchError):
    """A concurrent managed audit is already running for the same repo_id.

    Raised by acquire_audit_lock() when the per-repo lock is held.
    Callers must wait for the in-progress audit to complete before dispatching
    another for the same repo.

    ``held_payload`` carries the live lock payload for diagnostics — CLIs and
    operators surface it to show *which* run holds the lock.
    """

    held_payload: "object | None" = None

    def __init__(self, message: str, held_payload: "object | None" = None) -> None:
        super().__init__(message)
        self.held_payload = held_payload


class AuditDispatchConfigError(AuditDispatchError):
    """Dispatch cannot proceed due to a programming or configuration error.

    Raised (not returned) for conditions that indicate a caller bug or
    misconfiguration, such as a missing managed repo config, an unsupported
    audit_type, or a blocked command status.

    These are not operational failures — they indicate the dispatch was
    incorrectly configured and must be fixed before retrying.
    """


class LockStoreCorruptError(AuditDispatchError):
    """A persistent lock file exists but is malformed (bad JSON or schema).

    Recoverable via ``operations-center-audit unlock --repo X --force`` after
    operator inspection.
    """


class StaleLockReclaimedWarning(UserWarning):
    """Emitted when a stale persistent lock is reclaimed during normal dispatch.

    A warning rather than an error — reclaim is the intended recovery path
    after an OpsCenter crash.
    """
