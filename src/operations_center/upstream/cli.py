# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Upstream CLI commands.

Wired into the entrypoint at ``operations_center.entrypoints.upstream.main``.
Commands are kept here so they can be unit-tested directly without the
typer wrapper.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from operations_center.upstream.install import (
    InstallError,
    InstallMode,
    install_all,
    install_fork,
    verify_all,
)
from operations_center.upstream.registry import (
    RegistryError,
    load_registry,
)


def cmd_install(
    *,
    fork_id: Optional[str],
    mode: InstallMode,
    all_forks: bool,
    dry_run: bool,
    registry_path: Path | None = None,
) -> int:
    registry = load_registry(registry_path)
    if all_forks:
        results = install_all(registry, mode, dry_run=dry_run)
    else:
        if fork_id is None:
            print("ERROR: provide a fork_id or pass --all", file=sys.stderr)
            return 2
        try:
            entry = registry.get(fork_id)
        except RegistryError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        try:
            results = [install_fork(entry, mode, dry_run=dry_run)]
        except InstallError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    for r in results:
        prefix = "[DRY-RUN]" if dry_run else ("[OK]" if r.ok else "[FAIL]")
        print(f"{prefix} {r.fork_id} ({r.mode.value}): {r.command}")
        if r.stderr and not r.ok:
            print(f"    stderr: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ''}")
    return 0 if all(r.ok for r in results) else 1


def cmd_verify(*, registry_path: Path | None = None) -> int:
    """Exit non-zero on any drift; prints per-fork verdict."""
    registry = load_registry(registry_path)
    results = verify_all(registry)
    if not results:
        print("No forks registered.")
        return 0

    for r in results:
        marker = "[OK]   " if r.ok else "[FAIL] "
        print(f"{marker} {r.fork_id}: {r.status.value}")
        if not r.ok:
            print(f"         expected={r.expected_repo}@{r.expected_sha[:8]}")
            print(f"         observed={r.observed_repo}@{(r.observed_sha or 'none')[:8]}")
            if r.detail:
                print(f"         detail:  {r.detail}")
    return 0 if all(r.ok for r in results) else 1


def cmd_bump(*, fork_id: str, to_sha: Optional[str], registry_path: Path | None = None) -> int:
    from operations_center.upstream.lifecycle import LifecycleError, bump_fork
    try:
        result = bump_fork(fork_id, to_sha=to_sha, registry_path=registry_path)
    except LifecycleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[OK] {fork_id}: pinned {result.old_commit} -> {result.new_commit}")
    if result.patches_at_risk:
        print(f"     patches at risk (rebase needed): {', '.join(result.patches_at_risk)}")
    return 0


def cmd_rebase(*, fork_id: str, upstream_remote: str, registry_path: Path | None = None) -> int:
    from operations_center.upstream.lifecycle import LifecycleError, rebase_fork
    try:
        result = rebase_fork(fork_id, upstream_remote=upstream_remote, registry_path=registry_path)
    except LifecycleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    marker = "[OK]" if result.rebase_ok else "[FAIL]"
    print(f"{marker} {fork_id}: rebased onto {result.upstream_ref}")
    if not result.rebase_ok:
        print(f"     output: {result.rebase_output[-400:]}")
        return 1
    for patch_id, status in sorted(result.patch_status.items()):
        print(f"     {patch_id}: {status}")
    return 0


def cmd_sync(
    *, fork_id: str, mode_str: str, registry_path: Path | None = None,
    skip_install: bool = False,
) -> int:
    from operations_center.upstream.lifecycle import LifecycleError, sync_fork
    try:
        mode = InstallMode(mode_str)
    except ValueError:
        print(f"ERROR: invalid mode {mode_str!r}", file=sys.stderr)
        return 2
    try:
        result = sync_fork(fork_id, mode=mode, registry_path=registry_path, skip_install=skip_install)
    except LifecycleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"{fork_id}: rebase={'ok' if result.rebase.rebase_ok else 'FAIL'}")
    if not result.rebase.rebase_ok:
        return 1
    if result.bump:
        print(f"  bumped: {result.bump.old_commit} -> {result.bump.new_commit}")
    if not skip_install:
        print(f"  install: {'ok' if result.install_ok else 'FAIL'}")
        if not result.install_ok:
            return 1
    return 0


def cmd_auto_sync(
    *,
    fork_id: Optional[str],
    all_forks: bool,
    mode_str: str = "dev",
    dry_run: bool = False,
    registry_path: Path | None = None,
) -> int:
    """Silently apply safe reconcile actions. Cron-friendly.

    Exit codes:
      0 — clean (synced or no_op for all targets)
      1 — at least one fork blocked (manual intervention needed)
      2 — invalid args / registry error
    """
    from operations_center.upstream.lifecycle import (
        AutoSyncResult, auto_sync_all, auto_sync_fork,
    )
    try:
        mode = InstallMode(mode_str)
    except ValueError:
        print(f"ERROR: invalid mode {mode_str!r}", file=sys.stderr)
        return 2

    results: list[AutoSyncResult]
    if all_forks:
        results = auto_sync_all(mode=mode, registry_path=registry_path, dry_run=dry_run)
    else:
        if fork_id is None:
            print("ERROR: provide a fork_id or pass --all", file=sys.stderr)
            return 2
        try:
            results = [auto_sync_fork(fork_id, mode=mode,
                                      registry_path=registry_path, dry_run=dry_run)]
        except RegistryError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    any_blocked = False
    for r in results:
        marker = "[OK]   " if r.ok else "[BLOCK]"
        suffix = " (dry-run)" if dry_run else ""
        print(f"{marker} {r.fork_id}: {r.final_state}{suffix}")
        for action in r.actions_taken:
            print(f"         + {action}")
        for blocked in r.actions_blocked:
            print(f"         ! {blocked}")
            any_blocked = True
    return 1 if any_blocked else 0


def cmd_poll(
    *,
    registry_path: Path | None = None,
    json_output: bool = False,
) -> int:
    """Run one poll iteration; emits UPSTREAM_RECONCILE findings.
    Exit non-zero when any reconciliation is suggested (cron-friendly).
    """
    from operations_center.upstream.poll import poll_all
    findings = poll_all(registry_path=registry_path)

    if json_output:
        import json as _json
        print(_json.dumps([f.to_dict() for f in findings], indent=2, sort_keys=True))
    else:
        if not findings:
            print("No reconciliation suggestions.")
        for f in findings:
            print(f"[{f.suggestion.value}] {f.patch_id}")
            print(f"    reason: {f.reason}")
            if f.action_link:
                print(f"    link:   {f.action_link}")
    return 1 if findings else 0


def cmd_push(
    *,
    patch_id: str,
    dry_run: bool = False,
    registry_path: Path | None = None,
) -> int:
    from operations_center.upstream.push import PushError, push_patch
    try:
        result = push_patch(patch_id, registry_path=registry_path, dry_run=dry_run)
    except PushError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not result.pushed_branch:
        print(f"[FAIL] {patch_id}: {result.detail}")
        return 1
    if not result.pr_created:
        print(f"[FAIL] {patch_id}: branch pushed but PR creation failed: {result.detail}")
        return 1
    print(f"[OK] {patch_id}: branch pushed, PR opened at {result.pr_url}")
    return 0


def cmd_drop(*, patch_id: str, patches_root: Path | None = None) -> int:
    from operations_center.upstream.push import PushError, drop_patch
    try:
        drop_patch(patch_id, patches_root=patches_root)
    except PushError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[OK] {patch_id}: patch dropped (yaml removed)")
    print("     Remember to transition the matching contract_gap status")
    print("     (forked -> upstream_merged -> mitigated -> closed)")
    return 0


def cmd_status(*, registry_path: Path | None = None) -> int:
    """Per-fork installed/registry/divergence + patch summary."""
    registry = load_registry(registry_path)
    if not registry.entries:
        print("No forks registered.")
        return 0

    verifies = {v.fork_id: v for v in verify_all(registry)}
    for entry in registry.all():
        v = verifies[entry.fork_id]
        print(f"{entry.fork_id}")
        print(f"  upstream:  {entry.upstream.repo} (release={entry.upstream.latest_known_release or 'unknown'})")
        print(f"  fork:      {entry.fork.repo}@{entry.fork_commit[:8]} (branch={entry.fork.branch})")
        print(f"  base:      {entry.base_commit[:8]}")
        print(f"  install:   kind={entry.install.kind.value}; modes={sorted(m.value for m in entry.install.modes)}")
        print(f"  poll:      every {entry.poll_cadence_hours}h; auto_pr_push={entry.auto_pr_push}")
        print(f"  installed: {v.status.value}", end="")
        if v.observed_sha:
            print(f" (observed={v.observed_sha[:8]})")
        else:
            print()
        print()
    return 0
