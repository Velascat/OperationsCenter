# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
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
    """


class AuditDispatchConfigError(AuditDispatchError):
    """Dispatch cannot proceed due to a programming or configuration error.

    Raised (not returned) for conditions that indicate a caller bug or
    misconfiguration, such as a missing managed repo config, an unsupported
    audit_type, or a blocked command status.

    These are not operational failures — they indicate the dispatch was
    incorrectly configured and must be fixed before retrying.
    """
