# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""One-audit-per-repo lock registry, backed by a crash-safe persistent store.

Two layers:

* In-process ``threading.Lock`` for thread-safety within one OpsCenter process.
* On-disk ``PersistentLockStore`` for cross-process exclusion + crash-safety.

Public surface (preserved across the Phase 6 refactor):
    ``ManagedRepoAuditLock``, ``ManagedRepoAuditLockRegistry``,
    ``acquire_audit_lock(repo_id, ...)``, ``is_audit_locked(repo_id)``.

The legacy zero-arg ``acquire_audit_lock(repo_id)`` form is preserved for
unit tests that don't carry identity context — it synthesizes a payload
identifying the OpsCenter PID only.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from types import TracebackType

from .errors import RepoLockAlreadyHeldError
from .lock_store import PersistentLockPayload, PersistentLockStore

# OpsCenter root: locks live at <oc_root>/state/audit_dispatch/locks/.
# locks.py is at: src/operations_center/audit_dispatch/locks.py
# parents[0] = audit_dispatch/, [1] = operations_center/, [2] = src/, [3] = OC root
_OC_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_STATE_DIR = _OC_ROOT / "state" / "audit_dispatch" / "locks"


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class ManagedRepoAuditLock:
    """Context manager for a single-repo audit lock.

    Acquired via ``ManagedRepoAuditLockRegistry.acquire()``. Release is
    idempotent — double-releases are silently ignored.

    Use as a context manager (preferred) or call release() explicitly:

        lock = acquire_audit_lock("videofoundry", run_id=...)
        try:
            ...
        finally:
            lock.release()
    """

    __slots__ = ("_repo_id", "_registry", "_released", "_payload")

    def __init__(
        self,
        repo_id: str,
        registry: "ManagedRepoAuditLockRegistry",
        payload: PersistentLockPayload | None = None,
    ) -> None:
        self._repo_id = repo_id
        self._registry = registry
        self._released = False
        self._payload = payload

    @property
    def repo_id(self) -> str:
        return self._repo_id

    @property
    def payload(self) -> PersistentLockPayload | None:
        """The persistent-store payload backing this lock (None for legacy locks)."""
        return self._payload

    def update_audit_pid(self, audit_pid: int, audit_pgid: int | None = None) -> None:
        """Record the audit subprocess PID after the executor has spawned it.

        Safe to call multiple times. No-op if the lock has no persistent payload
        (legacy zero-arg locks).
        """
        if self._payload is None:
            return
        self._payload = self._registry._update_payload(
            self._repo_id, audit_pid=audit_pid, audit_pgid=audit_pgid
        )

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
    """Thread-safe lock registry backed by a crash-safe ``PersistentLockStore``.

    At most one managed audit per ``repo_id`` may be dispatched at a time.
    Acquiring a lock for a held ``repo_id`` raises ``RepoLockAlreadyHeldError``.
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        self._mutex = threading.Lock()
        self._held: set[str] = set()
        self._store = PersistentLockStore(state_dir or _DEFAULT_STATE_DIR)
        self._swept = False

    @property
    def store(self) -> PersistentLockStore:
        return self._store

    def acquire(
        self,
        repo_id: str,
        *,
        run_id: str | None = None,
        audit_type: str | None = None,
        oc_pid: int | None = None,
        command: str = "",
        expected_run_status_path: str = "",
    ) -> ManagedRepoAuditLock:
        """Acquire the lock for ``repo_id`` and persist a lock payload.

        Identity parameters are accepted; legacy zero-arg callers receive a
        synthesized payload (run_id="legacy", current OC PID).
        """
        self._sweep_once()
        with self._mutex:
            if repo_id in self._held:
                raise RepoLockAlreadyHeldError(
                    f"A managed audit is already running for repo '{repo_id}' "
                    "in this OpsCenter process. Only one audit per repo is "
                    "permitted at a time. Wait for the in-progress audit to "
                    "complete before dispatching another."
                )
            payload = PersistentLockPayload(
                repo_id=repo_id,
                run_id=run_id or "legacy",
                audit_type=audit_type or "unknown",
                oc_pid=oc_pid or os.getpid(),
                started_at=_now_iso(),
                command=command,
                expected_run_status_path=expected_run_status_path,
            )
            # try_acquire raises RepoLockAlreadyHeldError if disk lock is held.
            self._store.try_acquire(payload)
            self._held.add(repo_id)
        return ManagedRepoAuditLock(repo_id, self, payload=payload)

    def _release(self, repo_id: str) -> None:
        with self._mutex:
            self._held.discard(repo_id)
            try:
                self._store.release(repo_id)
            except Exception:
                # Release must never raise — best-effort cleanup.
                pass

    def _update_payload(
        self,
        repo_id: str,
        *,
        audit_pid: int | None = None,
        audit_pgid: int | None = None,
    ) -> PersistentLockPayload:
        return self._store.update(
            repo_id, audit_pid=audit_pid, audit_pgid=audit_pgid
        )

    def _sweep_once(self) -> None:
        """Reclaim stale locks once on first use of this registry."""
        if self._swept:
            return
        try:
            self._store.sweep_stale()
        finally:
            self._swept = True

    def is_held(self, repo_id: str) -> bool:
        """True iff a live persistent lock exists for ``repo_id``."""
        with self._mutex:
            if repo_id in self._held:
                return True
        # Allow another OpsCenter process holding the disk lock to count as held.
        try:
            existing = self._store.read(repo_id)
        except Exception:
            return False
        return existing is not None and existing.is_alive()

    @property
    def held_repos(self) -> frozenset[str]:
        """Snapshot of all locked repo_ids in this OpsCenter process."""
        with self._mutex:
            return frozenset(self._held)


# Process-scoped global registry. One audit per repo, per process lifetime.
_GLOBAL_REGISTRY = ManagedRepoAuditLockRegistry()


def acquire_audit_lock(
    repo_id: str,
    *,
    run_id: str | None = None,
    audit_type: str | None = None,
    oc_pid: int | None = None,
    command: str = "",
    expected_run_status_path: str = "",
) -> ManagedRepoAuditLock:
    """Acquire the global per-repo audit lock.

    Raises ``RepoLockAlreadyHeldError`` if the repo already has an audit
    dispatched (in this process or in another OpsCenter process holding the
    persistent disk lock).
    """
    return _GLOBAL_REGISTRY.acquire(
        repo_id,
        run_id=run_id,
        audit_type=audit_type,
        oc_pid=oc_pid,
        command=command,
        expected_run_status_path=expected_run_status_path,
    )


def is_audit_locked(repo_id: str) -> bool:
    """True if a managed audit is currently dispatched for ``repo_id``."""
    return _GLOBAL_REGISTRY.is_held(repo_id)


def get_global_registry() -> ManagedRepoAuditLockRegistry:
    """Return the process-scoped global registry (for CLI / introspection)."""
    return _GLOBAL_REGISTRY
