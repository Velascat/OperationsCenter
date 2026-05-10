# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Crash-safe on-disk lock store for managed-repo audit dispatch.

Each held lock is one JSON file at ``state/audit_dispatch/locks/{repo_id}.lock``
written atomically (tempfile + ``os.replace``). Cross-process exclusion is
enforced via ``fcntl.flock`` on a sentinel file beside the payload, using the
existing ``audit_governance.file_locks.locked_state_file`` helper.

Liveness model (dual-PID)
-------------------------
A held lock records two PIDs:

* ``oc_pid`` — the OperationsCenter process that dispatched the audit.
* ``audit_pid`` — the audit subprocess PID, populated once the executor has
  called ``Popen``. Until then it is ``None``.

A lock is considered **stale** (eligible for reclaim) iff *all* recorded PIDs
are dead. This means an audit subprocess orphaned by an OpsCenter crash holds
the lock until it exits, which is the correct behavior — we must not dispatch
a second audit while the first is still writing artifacts.

POSIX-only — uses ``os.kill(pid, 0)`` and ``fcntl``.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import (
    LockStoreCorruptError,
    RepoLockAlreadyHeldError,
    StaleLockReclaimedWarning,
)


def _locked_state_file(path: Path):
    """Lazy import shim — avoids circular import via audit_governance package init."""
    from operations_center.audit_governance.file_locks import locked_state_file
    return locked_state_file(path)

LOCK_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_pid_alive(pid: int | None) -> bool:
    """Return True if a process with this PID exists and we can signal it.

    Uses ``os.kill(pid, 0)`` — sends no signal, but raises if the PID is
    unknown (ESRCH) or unreachable (EPERM means it exists but we lack
    permission, so we conservatively consider it alive).
    """
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


@dataclass(frozen=True, slots=True)
class PersistentLockPayload:
    """Serialized contents of one ``{repo_id}.lock`` file.

    Frozen so the in-process registry can hand callers a snapshot without
    risk of mutation. To update fields on disk, call
    ``PersistentLockStore.update(repo_id, audit_pid=...)``.
    """

    repo_id: str
    run_id: str
    audit_type: str
    oc_pid: int
    started_at: str
    command: str
    expected_run_status_path: str
    audit_pid: int | None = None
    audit_pgid: int | None = None
    owner_hostname: str = field(default_factory=socket.gethostname)
    lock_schema_version: int = LOCK_SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "lock_schema_version": self.lock_schema_version,
            "repo_id": self.repo_id,
            "run_id": self.run_id,
            "audit_type": self.audit_type,
            "oc_pid": self.oc_pid,
            "audit_pid": self.audit_pid,
            "audit_pgid": self.audit_pgid,
            "started_at": self.started_at,
            "command": self.command,
            "expected_run_status_path": self.expected_run_status_path,
            "owner_hostname": self.owner_hostname,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PersistentLockPayload":
        try:
            return cls(
                repo_id=str(data["repo_id"]),
                run_id=str(data["run_id"]),
                audit_type=str(data["audit_type"]),
                oc_pid=int(data["oc_pid"]),
                started_at=str(data["started_at"]),
                command=str(data["command"]),
                expected_run_status_path=str(data["expected_run_status_path"]),
                audit_pid=(
                    int(data["audit_pid"])
                    if data.get("audit_pid") is not None
                    else None
                ),
                audit_pgid=(
                    int(data["audit_pgid"])
                    if data.get("audit_pgid") is not None
                    else None
                ),
                owner_hostname=str(data.get("owner_hostname", socket.gethostname())),
                lock_schema_version=int(
                    data.get("lock_schema_version", LOCK_SCHEMA_VERSION)
                ),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise LockStoreCorruptError(
                f"malformed lock payload: missing/invalid field ({exc})"
            ) from exc

    def is_alive(self) -> bool:
        """True if any recorded PID is alive (OC or audit subprocess)."""
        return _is_pid_alive(self.oc_pid) or _is_pid_alive(self.audit_pid)

    def liveness_summary(self) -> dict[str, bool]:
        return {
            "oc_pid_alive": _is_pid_alive(self.oc_pid),
            "audit_pid_alive": _is_pid_alive(self.audit_pid),
        }


class PersistentLockStore:
    """File-backed lock store at ``state/audit_dispatch/locks/``.

    Methods are safe for concurrent use across both threads (in-process) and
    OS processes (via ``fcntl.flock`` sentinel files).
    """

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def state_dir(self) -> Path:
        return self._state_dir

    def _lock_path(self, repo_id: str) -> Path:
        return self._state_dir / f"{repo_id}.lock"

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(self, repo_id: str) -> PersistentLockPayload | None:
        """Return the payload for repo_id, or None if no lock file exists.

        Raises LockStoreCorruptError if the file exists but is malformed.
        """
        path = self._lock_path(repo_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LockStoreCorruptError(
                f"lock file {path} is not valid JSON: {exc}"
            ) from exc
        return PersistentLockPayload.from_json(data)

    def _iter_lock_files(self) -> list[Path]:
        """Yield only first-tier ``{repo_id}.lock`` files, never sentinels.

        ``locked_state_file`` creates ``{name}.lock`` sentinels next to each
        target file. A naive ``glob("*.lock")`` would match those too and
        cause recursive sentinel creation. We filter to filenames whose stem
        contains no dot (i.e., ``foo.lock`` matches but ``foo.lock.lock`` does not).
        """
        out: list[Path] = []
        if not self._state_dir.exists():
            return out
        for path in sorted(self._state_dir.glob("*.lock")):
            if "." in path.stem:
                continue
            out.append(path)
        return out

    def list_active(self) -> list[PersistentLockPayload]:
        """All currently-held locks (corrupt files are skipped, not raised)."""
        out: list[PersistentLockPayload] = []
        for path in self._iter_lock_files():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                out.append(PersistentLockPayload.from_json(data))
            except (json.JSONDecodeError, LockStoreCorruptError):
                continue
        return out

    # ------------------------------------------------------------------
    # Acquire / release / update
    # ------------------------------------------------------------------

    def try_acquire(self, payload: PersistentLockPayload) -> PersistentLockPayload:
        """Acquire the lock for payload.repo_id and write payload to disk.

        Raises
        ------
        RepoLockAlreadyHeldError
            If another live lock already exists for this repo. The held
            payload is included on the exception via ``.held_payload``.
        """
        path = self._lock_path(payload.repo_id)
        with _locked_state_file(path):
            existing = self.read(payload.repo_id)
            if existing is not None and existing.is_alive():
                raise RepoLockAlreadyHeldError(
                    f"managed audit lock for repo {payload.repo_id!r} is held by "
                    f"run {existing.run_id} (oc_pid={existing.oc_pid}, "
                    f"audit_pid={existing.audit_pid})",
                    held_payload=existing,
                )
            self._write_atomic(path, payload)
        return payload

    def release(self, repo_id: str) -> bool:
        """Remove the lock file for repo_id. Idempotent; True if removed."""
        path = self._lock_path(repo_id)
        with _locked_state_file(path):
            if not path.exists():
                return False
            path.unlink()
            return True

    def update(self, repo_id: str, **changes: Any) -> PersistentLockPayload:
        """Atomically update specific fields on the held lock payload.

        Used to record ``audit_pid`` and ``audit_pgid`` after the executor
        has spawned the subprocess. Raises ``FileNotFoundError`` if the lock
        is not currently held.
        """
        path = self._lock_path(repo_id)
        with _locked_state_file(path):
            existing = self.read(repo_id)
            if existing is None:
                raise FileNotFoundError(
                    f"no lock to update for repo {repo_id!r}"
                )
            updated = replace(existing, **changes)
            self._write_atomic(path, updated)
        return updated

    def reclaim_if_stale(self, repo_id: str) -> bool:
        """Release the lock if all recorded PIDs are dead. True if reclaimed."""
        path = self._lock_path(repo_id)
        with _locked_state_file(path):
            try:
                existing = self.read(repo_id)
            except LockStoreCorruptError:
                # Corrupt file is treated as stale (we can't verify it,
                # but blocking dispatch on a corrupt file is worse).
                path.unlink(missing_ok=True)
                return True
            if existing is None:
                return False
            if existing.is_alive():
                return False
            path.unlink(missing_ok=True)
            return True

    def sweep_stale(self) -> list[str]:
        """Reclaim every stale lock under state_dir. Returns repo_ids reclaimed."""
        reclaimed: list[str] = []
        for path in self._iter_lock_files():
            repo_id = path.stem
            if self.reclaim_if_stale(repo_id):
                reclaimed.append(repo_id)
        return reclaimed

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _write_atomic(self, path: Path, payload: PersistentLockPayload) -> None:
        """Write payload as JSON via tempfile + os.replace (atomic on POSIX)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload.to_json(), indent=2, ensure_ascii=False)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f"{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.write("\n")
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


__all__ = [
    "LOCK_SCHEMA_VERSION",
    "PersistentLockPayload",
    "PersistentLockStore",
    "StaleLockReclaimedWarning",
]
