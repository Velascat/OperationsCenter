# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R3 lifecycle: bump / rebase / sync.

These operate on the local fork clone (resolved via dev-mode logic) and
update the registry's pinned ``fork_commit`` accordingly. Each command
returns a structured result so the CLI can surface what happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from operations_center.upstream import git_ops
from operations_center.upstream.install import install_fork
from operations_center.upstream.patches import Patch, load_patches
from operations_center.upstream.registry import (
    ForkEntry,
    InstallMode,
    RegistryError,
    load_registry,
    resolve_local_clone,
)


class LifecycleError(RuntimeError):
    """Raised when bump/rebase/sync hits a non-recoverable condition."""


@dataclass
class BumpResult:
    fork_id: str
    old_commit: str
    new_commit: str
    patches_at_risk: list[str] = field(default_factory=list)


@dataclass
class RebaseResult:
    fork_id: str
    upstream_remote: str
    upstream_ref: str
    rebase_ok: bool
    rebase_output: str
    patch_status: dict[str, str] = field(default_factory=dict)


@dataclass
class SyncResult:
    fork_id: str
    rebase: RebaseResult
    bump: Optional[BumpResult] = None
    install_ok: bool = False


# ── Bump ────────────────────────────────────────────────────────────────


def bump_fork(
    fork_id: str,
    *,
    to_sha: Optional[str] = None,
    registry_path: Path | None = None,
) -> BumpResult:
    """Pin the registry to the fork's current HEAD (or a specified SHA).

    Verifies that every patch's ``touched_files`` still exist at the new
    SHA — failures are returned in ``patches_at_risk`` for the caller to
    surface (does NOT block the bump; rebasing patches is the user's call).
    """
    registry_path = registry_path or _default_registry_path()
    registry = load_registry(registry_path)
    entry = registry.get(fork_id)

    clone = resolve_local_clone(entry)
    if clone is None:
        raise LifecycleError(
            f"{fork_id}: cannot bump — no local clone found. "
            "Set OC_UPSTREAM_CLONES_ROOT or local_clone_hint."
        )
    if not git_ops.is_clean(clone):
        raise LifecycleError(
            f"{fork_id}: clone at {clone} has uncommitted changes; "
            "commit or stash before bumping."
        )

    new_sha = to_sha or git_ops.head_sha(clone)[:7]
    old_sha = entry.fork_commit

    # Check patches against the new SHA
    patches = load_patches().for_fork(fork_id)
    at_risk = _patches_with_missing_touched_files(clone, patches)

    # Persist registry
    _rewrite_fork_commit(registry_path, fork_id, new_sha)

    return BumpResult(
        fork_id=fork_id,
        old_commit=old_sha,
        new_commit=new_sha,
        patches_at_risk=at_risk,
    )


def _patches_with_missing_touched_files(clone: Path, patches: list[Patch]) -> list[str]:
    out: list[str] = []
    for p in patches:
        for f in p.touched_files:
            if not (clone / f).exists():
                out.append(p.id)
                break
    return out


def _rewrite_fork_commit(registry_path: Path, fork_id: str, new_sha: str) -> None:
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    raw.setdefault("forks", {})
    if fork_id not in raw["forks"]:
        raise LifecycleError(f"registry has no entry for {fork_id!r}")
    raw["forks"][fork_id]["fork_commit"] = new_sha
    registry_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _default_registry_path() -> Path:
    from operations_center.upstream.registry import _DEFAULT_REGISTRY_PATH
    return _DEFAULT_REGISTRY_PATH


# ── Rebase ──────────────────────────────────────────────────────────────


def rebase_fork(
    fork_id: str,
    *,
    upstream_remote: str = "upstream",
    upstream_ref: Optional[str] = None,
    registry_path: Path | None = None,
) -> RebaseResult:
    """git fetch upstream, git rebase upstream/<branch>.

    Per-patch report: for each patch's ``touched_files``, did the rebase
    leave them present and conflict-free? The result captures the rebase
    subprocess output so the caller can surface conflicts.
    """
    registry_path = registry_path or _default_registry_path()
    registry = load_registry(registry_path)
    entry = registry.get(fork_id)

    clone = resolve_local_clone(entry)
    if clone is None:
        raise LifecycleError(f"{fork_id}: cannot rebase — no local clone found.")
    if not git_ops.is_clean(clone):
        raise LifecycleError(
            f"{fork_id}: clone at {clone} has uncommitted changes; "
            "commit or stash before rebasing."
        )

    target = upstream_ref or f"{upstream_remote}/{entry.fork.branch}"

    fetch_res = git_ops.fetch_upstream(clone, remote=upstream_remote)
    if not fetch_res.ok:
        return RebaseResult(
            fork_id=fork_id, upstream_remote=upstream_remote, upstream_ref=target,
            rebase_ok=False, rebase_output=f"fetch failed: {fetch_res.stderr.strip()}",
        )

    rebase_res = git_ops.rebase_onto(clone, target)
    output = (rebase_res.stdout + "\n" + rebase_res.stderr).strip()

    patches = load_patches().for_fork(fork_id)
    patch_status: dict[str, str] = {}
    for p in patches:
        missing = [f for f in p.touched_files if not (clone / f).exists()]
        if missing:
            patch_status[p.id] = f"missing_files:{missing}"
        else:
            patch_status[p.id] = "files_present"

    return RebaseResult(
        fork_id=fork_id, upstream_remote=upstream_remote, upstream_ref=target,
        rebase_ok=rebase_res.ok, rebase_output=output,
        patch_status=patch_status,
    )


# ── Sync ────────────────────────────────────────────────────────────────


def sync_fork(
    fork_id: str,
    *,
    mode: InstallMode = InstallMode.DEV,
    registry_path: Path | None = None,
    skip_install: bool = False,
) -> SyncResult:
    """rebase + bump + reinstall, in that order. Stops at first failure."""
    rebase = rebase_fork(fork_id, registry_path=registry_path)
    if not rebase.rebase_ok:
        return SyncResult(fork_id=fork_id, rebase=rebase)

    bump = bump_fork(fork_id, registry_path=registry_path)

    install_ok = True
    if not skip_install:
        registry = load_registry(registry_path or _default_registry_path())
        result = install_fork(registry.get(fork_id), mode)
        install_ok = result.ok

    return SyncResult(fork_id=fork_id, rebase=rebase, bump=bump, install_ok=install_ok)
