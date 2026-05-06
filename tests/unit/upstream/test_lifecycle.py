# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 14 R3 — bump / rebase / sync lifecycle tests.

Uses real local git repos in tmp_path so behavior matches production
without needing a remote. The "upstream" remote points at a sibling
local repo; "origin" can stay unset since these tests don't push.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from operations_center.upstream.lifecycle import (
    LifecycleError, bump_fork, rebase_fork,
)
from operations_center.upstream.registry import load_registry


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _init_repo(path: Path, *, with_files: dict[str, str] | None = None) -> str:
    """Initialize a git repo at ``path`` with optional initial files. Returns HEAD SHA."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@test")
    _git(path, "config", "user.name", "test")
    for filename, content in (with_files or {}).items():
        target = path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _git(path, "add", filename)
    _git(path, "commit", "-q", "-m", "init")
    return _git(path, "rev-parse", "HEAD").strip()


def _seed_kodo_clone(tmp_path: Path) -> tuple[Path, str]:
    """Create a fake kodo clone with the touched_files PATCH-001 references."""
    clone = tmp_path / "kodo"
    sha = _init_repo(clone, with_files={
        "kodo/orchestrators/claude_code.py": "# stub\n",
        "kodo/orchestrators/kimi_code.py": "# stub\n",
        "tests/orchestrators/test_orchestrator_signatures.py": "# stub\n",
    })
    return clone, sha


def _registry_with_kodo(tmp_path: Path, clone: Path, fork_commit: str) -> Path:
    """Registry pinned to fork_commit, hint pointing at the seeded clone."""
    reg = tmp_path / "registry.yaml"
    reg.write_text(
        f"""
forks:
  kodo:
    upstream:
      repo: ikamensh/kodo
    fork:
      repo: Velascat/kodo
      branch: dev
    base_commit: {fork_commit}
    fork_commit: {fork_commit}
    install:
      kind: cli_tool
      modes:
        prod: "uv tool install --reinstall --force git+ssh://git@github.com/Velascat/kodo.git@{{fork_commit}}"
        ci:   "uv tool install --reinstall --force git+https://github.com/Velascat/kodo.git@{{fork_commit}}"
        dev:  "uv tool install --reinstall --force {{local_clone}}"
      local_clone_hint: {clone}
""",
        encoding="utf-8",
    )
    return reg


# ── Bump ────────────────────────────────────────────────────────────────


class TestBump:
    def test_bump_to_head_updates_registry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        clone, initial_sha = _seed_kodo_clone(tmp_path)
        # Add a second commit so HEAD differs from initial
        (clone / "README.md").write_text("# kodo\n", encoding="utf-8")
        _git(clone, "add", "README.md")
        _git(clone, "commit", "-q", "-m", "add readme")
        new_head = _git(clone, "rev-parse", "HEAD").strip()
        registry_path = _registry_with_kodo(tmp_path, clone, initial_sha[:7])

        result = bump_fork("kodo", registry_path=registry_path)

        assert result.old_commit == initial_sha[:7]
        assert result.new_commit == new_head[:7]
        # patches_at_risk empty: no patches loaded in tmp scope
        assert result.patches_at_risk == []
        # Registry was rewritten
        reg = load_registry(registry_path)
        assert reg.get("kodo").fork_commit == new_head[:7]

    def test_bump_to_explicit_sha(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        clone, initial_sha = _seed_kodo_clone(tmp_path)
        registry_path = _registry_with_kodo(tmp_path, clone, initial_sha[:7])
        result = bump_fork("kodo", to_sha="deadbee", registry_path=registry_path)
        assert result.new_commit == "deadbee"
        reg = load_registry(registry_path)
        assert reg.get("kodo").fork_commit == "deadbee"

    def test_bump_blocked_when_clone_dirty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        clone, initial_sha = _seed_kodo_clone(tmp_path)
        (clone / "uncommitted.py").write_text("x = 1\n", encoding="utf-8")
        registry_path = _registry_with_kodo(tmp_path, clone, initial_sha[:7])
        with pytest.raises(LifecycleError, match="uncommitted"):
            bump_fork("kodo", registry_path=registry_path)

    def test_bump_no_clone_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        monkeypatch.delenv("OC_UPSTREAM_CLONES_ROOT", raising=False)
        registry_path = _registry_with_kodo(tmp_path, tmp_path / "absent", "abc1234")
        with pytest.raises(LifecycleError, match="no local clone"):
            bump_fork("kodo", registry_path=registry_path)


# ── Rebase ──────────────────────────────────────────────────────────────


class TestRebase:
    def test_rebase_against_local_upstream_succeeds_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        # Create an "upstream" repo and a clone of it as "fork"
        upstream = tmp_path / "upstream-kodo"
        _init_repo(upstream, with_files={"kodo/orchestrators/claude_code.py": "# upstream\n"})
        # Clone as fork
        clone = tmp_path / "kodo"
        subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
        _git(clone, "config", "user.email", "t@t")
        _git(clone, "config", "user.name", "t")
        _git(clone, "remote", "add", "upstream", str(upstream))
        _git(clone, "fetch", "-q", "upstream")
        _git(clone, "branch", "dev", "upstream/master")
        _git(clone, "checkout", "-q", "dev")
        head = _git(clone, "rev-parse", "HEAD").strip()
        registry_path = _registry_with_kodo(tmp_path, clone, head[:7])
        # Update registry's fork.branch to match
        registry_path.write_text(
            registry_path.read_text(encoding="utf-8").replace("branch: dev", "branch: master"),
            encoding="utf-8",
        )

        result = rebase_fork("kodo", upstream_remote="upstream", registry_path=registry_path)
        assert result.rebase_ok
        assert result.upstream_ref == "upstream/master"

    def test_rebase_blocked_when_clone_dirty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        clone, initial_sha = _seed_kodo_clone(tmp_path)
        (clone / "dirty.py").write_text("x\n", encoding="utf-8")
        registry_path = _registry_with_kodo(tmp_path, clone, initial_sha[:7])
        with pytest.raises(LifecycleError, match="uncommitted"):
            rebase_fork("kodo", registry_path=registry_path)
