# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Phase 6: Dispatch-orchestrated run control for managed repo audits.

Public surface
--------------
dispatch_managed_audit(request, *, config_dir, log_dir)
    Prepare, execute, and discover results of a managed audit run.

ManagedAuditDispatchRequest
    Request shape for dispatching a single managed audit.

ManagedAuditDispatchResult
    Canonical result with run_id, status, process exit code, and discovered paths.

DispatchStatus / FailureKind
    Structured status and failure-mode enums for result inspection.

acquire_audit_lock(repo_id)
    Acquire the global per-repo lock (raises RepoLockAlreadyHeldError if held).

AuditDispatchConfigError
    Raised when dispatch cannot be configured (programming / config error).

RepoLockAlreadyHeldError
    Raised when a concurrent audit is already running for the same repo.
"""

from .api import dispatch_managed_audit
from .errors import (
    AuditDispatchConfigError,
    AuditDispatchError,
    LockStoreCorruptError,
    RepoLockAlreadyHeldError,
    StaleLockReclaimedWarning,
)
from .lock_store import (
    LOCK_SCHEMA_VERSION,
    PersistentLockPayload,
    PersistentLockStore,
)
from .locks import (
    ManagedRepoAuditLock,
    ManagedRepoAuditLockRegistry,
    acquire_audit_lock,
    get_global_registry,
    is_audit_locked,
)
from .models import (
    DispatchStatus,
    FailureKind,
    ManagedAuditDispatchRequest,
    ManagedAuditDispatchResult,
)
from .watcher import RunStatusSnapshot, poll_run_status

__all__ = [
    "dispatch_managed_audit",
    "AuditDispatchError",
    "AuditDispatchConfigError",
    "RepoLockAlreadyHeldError",
    "LockStoreCorruptError",
    "StaleLockReclaimedWarning",
    "LOCK_SCHEMA_VERSION",
    "PersistentLockPayload",
    "PersistentLockStore",
    "ManagedRepoAuditLock",
    "ManagedRepoAuditLockRegistry",
    "acquire_audit_lock",
    "get_global_registry",
    "is_audit_locked",
    "DispatchStatus",
    "FailureKind",
    "ManagedAuditDispatchRequest",
    "ManagedAuditDispatchResult",
    "RunStatusSnapshot",
    "poll_run_status",
]
