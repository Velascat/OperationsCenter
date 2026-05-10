# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for Phase 6 per-repo audit lock registry."""

from __future__ import annotations

import threading

import pytest

import tempfile

from operations_center.audit_dispatch.errors import RepoLockAlreadyHeldError
from operations_center.audit_dispatch.locks import (
    ManagedRepoAuditLock,
    ManagedRepoAuditLockRegistry,
)


def _registry() -> ManagedRepoAuditLockRegistry:
    """Build a registry with an isolated temp state_dir so tests don't pollute
    the real OpsCenter state/audit_dispatch/locks/ directory."""
    return ManagedRepoAuditLockRegistry(state_dir=tempfile.mkdtemp())


class TestLockAcquire:
    def test_acquire_returns_lock(self) -> None:
        reg = _registry()
        lock = reg.acquire("example_managed_repo")
        assert isinstance(lock, ManagedRepoAuditLock)
        lock.release()

    def test_lock_repo_id(self) -> None:
        reg = _registry()
        lock = reg.acquire("example_managed_repo")
        assert lock.repo_id == "example_managed_repo"
        lock.release()

    def test_double_acquire_same_repo_raises(self) -> None:
        reg = _registry()
        lock = reg.acquire("example_managed_repo")
        with pytest.raises(RepoLockAlreadyHeldError, match="example_managed_repo"):
            reg.acquire("example_managed_repo")
        lock.release()

    def test_different_repos_can_be_acquired_concurrently(self) -> None:
        reg = _registry()
        lock_a = reg.acquire("example_managed_repo")
        lock_b = reg.acquire("other_repo")
        assert reg.is_held("example_managed_repo")
        assert reg.is_held("other_repo")
        lock_a.release()
        lock_b.release()

    def test_acquire_after_release_succeeds(self) -> None:
        reg = _registry()
        lock = reg.acquire("example_managed_repo")
        lock.release()
        lock2 = reg.acquire("example_managed_repo")
        assert reg.is_held("example_managed_repo")
        lock2.release()


class TestLockRelease:
    def test_release_clears_held(self) -> None:
        reg = _registry()
        lock = reg.acquire("example_managed_repo")
        assert reg.is_held("example_managed_repo")
        lock.release()
        assert not reg.is_held("example_managed_repo")

    def test_double_release_is_idempotent(self) -> None:
        reg = _registry()
        lock = reg.acquire("example_managed_repo")
        lock.release()
        lock.release()  # should not raise
        assert not reg.is_held("example_managed_repo")

    def test_context_manager_releases_on_exit(self) -> None:
        reg = _registry()
        with reg.acquire("example_managed_repo"):
            assert reg.is_held("example_managed_repo")
        assert not reg.is_held("example_managed_repo")

    def test_context_manager_releases_on_exception(self) -> None:
        reg = _registry()
        try:
            with reg.acquire("example_managed_repo"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert not reg.is_held("example_managed_repo")


class TestRegistryState:
    def test_is_held_false_initially(self) -> None:
        reg = _registry()
        assert not reg.is_held("example_managed_repo")

    def test_held_repos_empty_initially(self) -> None:
        reg = _registry()
        assert reg.held_repos == frozenset()

    def test_held_repos_reflects_acquired_locks(self) -> None:
        reg = _registry()
        lock_a = reg.acquire("repo_a")
        lock_b = reg.acquire("repo_b")
        assert reg.held_repos == frozenset({"repo_a", "repo_b"})
        lock_a.release()
        assert reg.held_repos == frozenset({"repo_b"})
        lock_b.release()
        assert reg.held_repos == frozenset()

    def test_held_repos_is_snapshot_not_live(self) -> None:
        reg = _registry()
        lock = reg.acquire("example_managed_repo")
        snapshot = reg.held_repos
        lock.release()
        # snapshot is a frozenset taken before release — it remains unchanged
        assert "example_managed_repo" in snapshot


class TestLazyStaleSweep:
    """Slice C: registry reclaims stale locks on first use (post-OC-restart safety)."""

    def test_sweep_clears_dead_lock_before_first_acquire(self, tmp_path) -> None:
        import subprocess
        import sys

        from operations_center.audit_dispatch.lock_store import (
            PersistentLockPayload,
            PersistentLockStore,
        )

        # Plant a stale lock from a "previous OC process" using a real-then-dead PID.
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        store = PersistentLockStore(tmp_path)
        store._write_atomic(
            tmp_path / "example_managed_repo.lock",
            PersistentLockPayload(
                repo_id="example_managed_repo",
                run_id="leftover",
                audit_type="audit_type_1",
                oc_pid=proc.pid,
                started_at="2026-05-04T00:00:00Z",
                command="x",
                expected_run_status_path="/tmp/x",
            ),
        )

        # Fresh registry (simulates OC restart) — first acquire sweeps + succeeds.
        reg = ManagedRepoAuditLockRegistry(state_dir=tmp_path)
        lock = reg.acquire("example_managed_repo", run_id="new")
        assert lock.payload is not None
        assert lock.payload.run_id == "new"
        lock.release()

    def test_sweep_only_runs_once_per_registry(self, tmp_path) -> None:
        reg = ManagedRepoAuditLockRegistry(state_dir=tmp_path)
        # Trigger sweep
        lock1 = reg.acquire("repo_a")
        lock1.release()
        # Plant a stale lock after the registry has already swept once
        import subprocess
        import sys

        from operations_center.audit_dispatch.lock_store import (
            PersistentLockPayload,
            PersistentLockStore,
        )

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        store = PersistentLockStore(tmp_path)
        store._write_atomic(
            tmp_path / "example_managed_repo.lock",
            PersistentLockPayload(
                repo_id="example_managed_repo",
                run_id="leftover",
                audit_type="audit_type_1",
                oc_pid=proc.pid,
                started_at="2026-05-04T00:00:00Z",
                command="x",
                expected_run_status_path="/tmp/x",
            ),
        )
        # The lazy sweep won't run again — but try_acquire will still detect
        # the stale lock at acquire time because is_alive() returns False.
        lock2 = reg.acquire("example_managed_repo", run_id="new")
        assert lock2.payload is not None
        assert lock2.payload.run_id == "new"
        lock2.release()


class TestIdentityWiring:
    """Slice B: lock payload carries run_id / audit_type / pids."""

    def test_acquire_payload_records_identity(self, tmp_path) -> None:
        import os

        reg = ManagedRepoAuditLockRegistry(state_dir=tmp_path)
        lock = reg.acquire(
            "example_managed_repo",
            run_id="vid_rep_xyz",
            audit_type="audit_type_1",
            command="python -m foo",
            expected_run_status_path="/tmp/x",
        )
        assert lock.payload is not None
        assert lock.payload.run_id == "vid_rep_xyz"
        assert lock.payload.audit_type == "audit_type_1"
        assert lock.payload.oc_pid == os.getpid()
        assert lock.payload.audit_pid is None
        lock.release()

    def test_update_audit_pid_persists(self, tmp_path) -> None:
        reg = ManagedRepoAuditLockRegistry(state_dir=tmp_path)
        lock = reg.acquire("example_managed_repo", run_id="r1")
        lock.update_audit_pid(audit_pid=4242, audit_pgid=4242)
        assert lock.payload is not None
        assert lock.payload.audit_pid == 4242
        on_disk = reg.store.read("example_managed_repo")
        assert on_disk is not None and on_disk.audit_pid == 4242
        lock.release()


class TestThreadSafety:
    def test_concurrent_acquires_for_same_repo_only_one_succeeds(self) -> None:
        reg = _registry()
        successes = []
        failures = []
        barrier = threading.Barrier(5)

        def attempt():
            barrier.wait()
            try:
                lock = reg.acquire("example_managed_repo")
                successes.append(lock)
            except RepoLockAlreadyHeldError:
                failures.append(True)

        threads = [threading.Thread(target=attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1
        assert len(failures) == 4
        successes[0].release()
