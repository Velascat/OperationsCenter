# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""In-memory one-audit-per-repo lock registry.

Policy: at most one managed audit per repo_id may be dispatched at a time.

Crash-safety note:
    This implementation holds lock state in-memory. If the OperationsCenter
    process crashes while an audit is running, all held locks are dropped on
    restart — stale locks do not persist. Process-crash-safe distributed locks
    are intentionally out of scope for Phase 6.
"""

from __future__ import annotations

import threading
from types import TracebackType

from .errors import RepoLockAlreadyHeldError


class ManagedRepoAuditLock:
    """Context manager for a single-repo audit lock.

    Acquired via ManagedRepoAuditLockRegistry.acquire().
    Release is idempotent — double-releases are silently ignored.

    Use as a context manager (preferred) or call release() explicitly:

        lock = acquire_audit_lock("videofoundry")
        try:
            ...
        finally:
            lock.release()
    """

    __slots__ = ("_repo_id", "_registry", "_released")

    def __init__(self, repo_id: str, registry: "ManagedRepoAuditLockRegistry") -> None:
        self._repo_id = repo_id
        self._registry = registry
        self._released = False

    @property
    def repo_id(self) -> str:
        return self._repo_id

    def release(self) -> None:
        """Release the lock. Safe to call multiple times."""
        if not self._released:
            self._released = True
            self._registry._release(self._repo_id)

    def __enter__(self) -> "ManagedRepoAuditLock":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def __repr__(self) -> str:
        state = "released" if self._released else "held"
        return f"ManagedRepoAuditLock(repo_id={self._repo_id!r}, state={state!r})"


class ManagedRepoAuditLockRegistry:
    """Thread-safe in-memory lock registry.

    Maintains a set of repo_ids for which an audit is currently dispatched.
    Acquiring a lock for a repo_id that is already held raises
    RepoLockAlreadyHeldError immediately — no waiting or queueing.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._held: set[str] = set()

    def acquire(self, repo_id: str) -> ManagedRepoAuditLock:
        """Acquire the lock for repo_id.

        Returns a ManagedRepoAuditLock context manager.

        Raises
        ------
        RepoLockAlreadyHeldError
            If a managed audit is already dispatched for this repo_id.
        """
        with self._mutex:
            if repo_id in self._held:
                raise RepoLockAlreadyHeldError(
                    f"A managed audit is already running for repo '{repo_id}'. "
                    f"Only one audit per repo is permitted at a time. "
                    f"Wait for the in-progress audit to complete before dispatching another."
                )
            self._held.add(repo_id)
        return ManagedRepoAuditLock(repo_id, self)

    def _release(self, repo_id: str) -> None:
        with self._mutex:
            self._held.discard(repo_id)

    def is_held(self, repo_id: str) -> bool:
        """Return True if an audit is currently dispatched for repo_id."""
        with self._mutex:
            return repo_id in self._held

    @property
    def held_repos(self) -> frozenset[str]:
        """Snapshot of all currently locked repo_ids."""
        with self._mutex:
            return frozenset(self._held)


# Process-scoped global registry. One audit per repo, per process lifetime.
_GLOBAL_REGISTRY = ManagedRepoAuditLockRegistry()


def acquire_audit_lock(repo_id: str) -> ManagedRepoAuditLock:
    """Acquire the global per-repo audit lock.

    Raises RepoLockAlreadyHeldError if the repo already has an audit dispatched.
    """
    return _GLOBAL_REGISTRY.acquire(repo_id)


def is_audit_locked(repo_id: str) -> bool:
    """Return True if a managed audit is currently dispatched for repo_id."""
    return _GLOBAL_REGISTRY.is_held(repo_id)
