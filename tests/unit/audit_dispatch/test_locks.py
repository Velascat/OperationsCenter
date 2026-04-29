# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for Phase 6 per-repo audit lock registry."""

from __future__ import annotations

import threading

import pytest

from operations_center.audit_dispatch.errors import RepoLockAlreadyHeldError
from operations_center.audit_dispatch.locks import (
    ManagedRepoAuditLock,
    ManagedRepoAuditLockRegistry,
)


def _registry() -> ManagedRepoAuditLockRegistry:
    return ManagedRepoAuditLockRegistry()


class TestLockAcquire:
    def test_acquire_returns_lock(self) -> None:
        reg = _registry()
        lock = reg.acquire("videofoundry")
        assert isinstance(lock, ManagedRepoAuditLock)
        lock.release()

    def test_lock_repo_id(self) -> None:
        reg = _registry()
        lock = reg.acquire("videofoundry")
        assert lock.repo_id == "videofoundry"
        lock.release()

    def test_double_acquire_same_repo_raises(self) -> None:
        reg = _registry()
        lock = reg.acquire("videofoundry")
        with pytest.raises(RepoLockAlreadyHeldError, match="videofoundry"):
            reg.acquire("videofoundry")
        lock.release()

    def test_different_repos_can_be_acquired_concurrently(self) -> None:
        reg = _registry()
        lock_a = reg.acquire("videofoundry")
        lock_b = reg.acquire("other_repo")
        assert reg.is_held("videofoundry")
        assert reg.is_held("other_repo")
        lock_a.release()
        lock_b.release()

    def test_acquire_after_release_succeeds(self) -> None:
        reg = _registry()
        lock = reg.acquire("videofoundry")
        lock.release()
        lock2 = reg.acquire("videofoundry")
        assert reg.is_held("videofoundry")
        lock2.release()


class TestLockRelease:
    def test_release_clears_held(self) -> None:
        reg = _registry()
        lock = reg.acquire("videofoundry")
        assert reg.is_held("videofoundry")
        lock.release()
        assert not reg.is_held("videofoundry")

    def test_double_release_is_idempotent(self) -> None:
        reg = _registry()
        lock = reg.acquire("videofoundry")
        lock.release()
        lock.release()  # should not raise
        assert not reg.is_held("videofoundry")

    def test_context_manager_releases_on_exit(self) -> None:
        reg = _registry()
        with reg.acquire("videofoundry"):
            assert reg.is_held("videofoundry")
        assert not reg.is_held("videofoundry")

    def test_context_manager_releases_on_exception(self) -> None:
        reg = _registry()
        try:
            with reg.acquire("videofoundry"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert not reg.is_held("videofoundry")


class TestRegistryState:
    def test_is_held_false_initially(self) -> None:
        reg = _registry()
        assert not reg.is_held("videofoundry")

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
        lock = reg.acquire("videofoundry")
        snapshot = reg.held_repos
        lock.release()
        # snapshot is a frozenset taken before release — it remains unchanged
        assert "videofoundry" in snapshot


class TestThreadSafety:
    def test_concurrent_acquires_for_same_repo_only_one_succeeds(self) -> None:
        reg = _registry()
        successes = []
        failures = []
        barrier = threading.Barrier(5)

        def attempt():
            barrier.wait()
            try:
                lock = reg.acquire("videofoundry")
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
