# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the persistent lock store (Phase 6, Slice A)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from operations_center.audit_dispatch.errors import (
    LockStoreCorruptError,
    RepoLockAlreadyHeldError,
)
from operations_center.audit_dispatch.lock_store import (
    LOCK_SCHEMA_VERSION,
    PersistentLockPayload,
    PersistentLockStore,
)


def _payload(
    *,
    repo_id: str = "example_managed_repo",
    run_id: str = "example_managed_repo_audit_type_1_20260504T120000Z_aabb1122",
    audit_type: str = "audit_type_1",
    oc_pid: int | None = None,
    audit_pid: int | None = None,
    audit_pgid: int | None = None,
) -> PersistentLockPayload:
    return PersistentLockPayload(
        repo_id=repo_id,
        run_id=run_id,
        audit_type=audit_type,
        oc_pid=oc_pid if oc_pid is not None else os.getpid(),
        audit_pid=audit_pid,
        audit_pgid=audit_pgid,
        started_at="2026-05-04T12:00:00Z",
        command="python -m tools.audit.run_representative_audit",
        expected_run_status_path="/tmp/run_status.json",
    )


def _spawn_short_subprocess() -> subprocess.Popen:
    """Spawn a Python child that exits quickly. Used to obtain a known PID."""
    return subprocess.Popen([sys.executable, "-c", "pass"])


class TestAcquireRelease:
    def test_acquire_writes_lock_file(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        store.try_acquire(_payload())
        assert (tmp_path / "example_managed_repo.lock").exists()

    def test_lock_file_contains_payload_fields(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        store.try_acquire(_payload(audit_pid=12345, audit_pgid=12345))
        data = json.loads((tmp_path / "example_managed_repo.lock").read_text())
        assert data["repo_id"] == "example_managed_repo"
        assert data["audit_type"] == "audit_type_1"
        assert data["audit_pid"] == 12345
        assert data["audit_pgid"] == 12345
        assert data["lock_schema_version"] == LOCK_SCHEMA_VERSION

    def test_release_removes_file(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        store.try_acquire(_payload())
        assert store.release("example_managed_repo") is True
        assert not (tmp_path / "example_managed_repo.lock").exists()

    def test_release_idempotent(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        assert store.release("example_managed_repo") is False  # never held

    def test_double_acquire_raises_when_oc_pid_alive(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        store.try_acquire(_payload())  # oc_pid = current process — alive
        with pytest.raises(RepoLockAlreadyHeldError) as ei:
            store.try_acquire(_payload())
        # Held payload is attached for diagnostics.
        held = ei.value.held_payload
        assert held is not None
        assert held.repo_id == "example_managed_repo"

    def test_acquire_succeeds_when_held_lock_is_dead(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        # Spawn + reap a child; its PID is now guaranteed dead.
        proc = _spawn_short_subprocess()
        proc.wait()
        dead_pid = proc.pid
        # Manually plant a lock with a dead PID — bypass try_acquire's liveness check.
        store._write_atomic(
            tmp_path / "example_managed_repo.lock",
            _payload(oc_pid=dead_pid),
        )
        # Now acquiring should succeed because the held lock's only PID is dead.
        store.try_acquire(_payload())
        assert (tmp_path / "example_managed_repo.lock").exists()

    def test_acquire_blocked_when_audit_pid_alive_even_if_oc_dead(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        proc = _spawn_short_subprocess()
        proc.wait()
        dead_pid = proc.pid
        # OC PID dead but audit subprocess alive (= current process).
        store._write_atomic(
            tmp_path / "example_managed_repo.lock",
            _payload(oc_pid=dead_pid, audit_pid=os.getpid()),
        )
        with pytest.raises(RepoLockAlreadyHeldError):
            store.try_acquire(_payload())


class TestUpdate:
    def test_update_audit_pid_atomically(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        store.try_acquire(_payload())
        updated = store.update("example_managed_repo", audit_pid=4242, audit_pgid=4242)
        assert updated.audit_pid == 4242
        assert updated.audit_pgid == 4242
        on_disk = store.read("example_managed_repo")
        assert on_disk is not None and on_disk.audit_pid == 4242

    def test_update_missing_lock_raises(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        with pytest.raises(FileNotFoundError):
            store.update("example_managed_repo", audit_pid=99)


class TestStaleReclaim:
    def test_reclaim_if_stale_when_pids_dead(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        proc = _spawn_short_subprocess()
        proc.wait()
        store._write_atomic(
            tmp_path / "example_managed_repo.lock",
            _payload(oc_pid=proc.pid),
        )
        assert store.reclaim_if_stale("example_managed_repo") is True
        assert not (tmp_path / "example_managed_repo.lock").exists()

    def test_reclaim_returns_false_when_alive(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        store.try_acquire(_payload())  # oc_pid = current = alive
        assert store.reclaim_if_stale("example_managed_repo") is False
        assert (tmp_path / "example_managed_repo.lock").exists()

    def test_reclaim_corrupt_lock_treated_as_stale(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        (tmp_path / "example_managed_repo.lock").write_text("{not valid json")
        assert store.reclaim_if_stale("example_managed_repo") is True

    def test_sweep_stale_returns_reclaimed_repos(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        proc = _spawn_short_subprocess()
        proc.wait()
        store._write_atomic(tmp_path / "repo_a.lock", _payload(repo_id="repo_a", oc_pid=proc.pid))
        store._write_atomic(tmp_path / "repo_b.lock", _payload(repo_id="repo_b", oc_pid=os.getpid()))
        reclaimed = store.sweep_stale()
        assert reclaimed == ["repo_a"]
        assert (tmp_path / "repo_b.lock").exists()


class TestRead:
    def test_read_returns_none_when_missing(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        assert store.read("nonexistent") is None

    def test_read_corrupt_raises(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        (tmp_path / "x.lock").write_text("{not valid json")
        with pytest.raises(LockStoreCorruptError):
            store.read("x")

    def test_read_missing_field_raises(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        (tmp_path / "x.lock").write_text(json.dumps({"repo_id": "x"}))
        with pytest.raises(LockStoreCorruptError):
            store.read("x")

    def test_list_active_returns_all_locks(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        store.try_acquire(_payload(repo_id="repo_a"))
        store.try_acquire(_payload(repo_id="repo_b"))
        active = store.list_active()
        assert {p.repo_id for p in active} == {"repo_a", "repo_b"}

    def test_list_active_skips_corrupt(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        store.try_acquire(_payload(repo_id="repo_a"))
        (tmp_path / "broken.lock").write_text("not json")
        active = store.list_active()
        assert {p.repo_id for p in active} == {"repo_a"}


class TestPayloadLiveness:
    def test_is_alive_when_oc_pid_alive(self) -> None:
        p = _payload(oc_pid=os.getpid(), audit_pid=None)
        assert p.is_alive() is True

    def test_is_alive_when_audit_pid_alive(self) -> None:
        p = _payload(oc_pid=99999999, audit_pid=os.getpid())
        assert p.is_alive() is True

    def test_is_dead_when_both_pids_dead(self) -> None:
        proc = _spawn_short_subprocess()
        proc.wait()
        p = _payload(oc_pid=proc.pid, audit_pid=None)
        assert p.is_alive() is False

    def test_liveness_summary_shape(self) -> None:
        p = _payload(oc_pid=os.getpid(), audit_pid=None)
        summary = p.liveness_summary()
        assert summary["oc_pid_alive"] is True
        assert summary["audit_pid_alive"] is False
