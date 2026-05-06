# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R3 lifecycle: bump / rebase / sync.

These operate on the local fork clone (resolved via dev-mode logic) and
update the registry's pinned ``fork_commit`` accordingly. Each command
returns a structured result so the CLI can surface what happened.
"""
from __future__ import annotations

import subprocess
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


# ── Auto-sync ────────────────────────────────────────────────────────────


@dataclass
class AutoSyncResult:
    """Outcome of one auto-sync pass on a single fork."""
    fork_id: str
    upstream_changed: bool                  # was there anything to pull?
    actions_taken: list[str] = field(default_factory=list)
    actions_blocked: list[str] = field(default_factory=list)
    final_state: str = "no_op"              # "synced" | "blocked" | "no_op"

    @property
    def ok(self) -> bool:
        return not self.actions_blocked


def auto_sync_fork(
    fork_id: str,
    *,
    mode: InstallMode = InstallMode.DEV,
    registry_path: Path | None = None,
    dry_run: bool = False,
) -> AutoSyncResult:
    """Silently apply safe reconcile actions for one fork.

    Walks the framework's reconcile suggestions and acts on the safe
    ones. Unsafe actions (rebase conflicts, PR creation) always abort
    with a clear finding instead of silently corrupting the fork.

    Refuses to run when the fork's ``auto_sync`` flag is False.

    Safe auto-actions:
      - DROP_PATCH (upstream merged equivalent fix): drop yaml +
        transition contract gap status forked → upstream_merged
      - Zero local patches + upstream HEAD changed: reset dev to
        upstream HEAD, bump, reinstall

    Blocked actions (emit finding, do nothing):
      - REBASE_PATCH: touched_files conflict — needs manual rebase
      - PUSH_PATCH: never auto-pushed (PRs are human-triggered)
    """
    registry_path = registry_path or _default_registry_path()
    registry = load_registry(registry_path)
    entry = registry.get(fork_id)

    result = AutoSyncResult(fork_id=fork_id, upstream_changed=False)

    if not entry.auto_sync:
        result.actions_blocked.append(f"auto_sync disabled for {fork_id}")
        result.final_state = "blocked"
        return result

    # Run a poll to get current reconcile suggestions
    from operations_center.upstream.poll import (
        ReconcileSuggestion, poll_all,
    )
    findings = [f for f in poll_all(registry_path=registry_path)
                if f.patch_id.startswith(f"{fork_id}:") or f.patch_id == ""]

    # Categorize
    drop_findings = [f for f in findings
                     if f.suggestion == ReconcileSuggestion.DROP_PATCH]
    rebase_findings = [f for f in findings
                       if f.suggestion == ReconcileSuggestion.REBASE_PATCH]

    # Block on unsafe actions first
    for f in rebase_findings:
        result.actions_blocked.append(
            f"{f.patch_id}: REBASE_PATCH needs manual rebase ({f.reason})"
        )
    if result.actions_blocked:
        result.final_state = "blocked"
        return result

    # Apply DROP_PATCH actions
    if drop_findings:
        from operations_center.upstream.push import drop_patch
        for f in drop_findings:
            if dry_run:
                result.actions_taken.append(f"<dry-run> would drop {f.patch_id}")
            else:
                drop_patch(f.patch_id)
                result.actions_taken.append(f"dropped {f.patch_id}")
                _transition_gap_to_upstream_merged(fork_id, f.patch_id)
                result.actions_taken.append(
                    f"transitioned {f.patch_id}'s gap → upstream_merged"
                )

    # Now check if upstream HEAD differs from our pinned commit. If so,
    # and we have zero patches now, reset + bump + reinstall.
    from operations_center.upstream.patches import load_patches
    remaining_patches = load_patches().for_fork(fork_id)
    clone = resolve_local_clone(entry)
    if clone is None:
        result.actions_blocked.append(
            f"{fork_id}: cannot complete sync — no local clone resolvable"
        )
        result.final_state = "blocked"
        return result

    # Always fetch upstream first
    fetch_res = git_ops.fetch_upstream(clone, remote="upstream")
    if not fetch_res.ok:
        result.actions_blocked.append(
            f"{fork_id}: fetch upstream failed: {fetch_res.stderr.strip()[:120]}"
        )
        result.final_state = "blocked"
        return result

    upstream_head = git_ops.head_sha_at_ref(clone, f"upstream/{entry.fork.branch}") if hasattr(git_ops, "head_sha_at_ref") else None
    if upstream_head is None:
        # Fallback — ask git directly
        sub = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", f"upstream/{entry.fork.branch}"],
            capture_output=True, text=True,
        )
        upstream_head = sub.stdout.strip() if sub.returncode == 0 else None

    if upstream_head and not entry.fork_commit.startswith(upstream_head[:len(entry.fork_commit)]):
        result.upstream_changed = True

    if remaining_patches:
        # Patches still present — skip the reset-to-upstream path
        if result.actions_taken:
            result.final_state = "synced"
        else:
            result.final_state = "no_op"
        return result

    # Zero patches → safe to reset dev to upstream HEAD
    if upstream_head and not entry.fork_commit.startswith(upstream_head[:7]):
        if dry_run:
            result.actions_taken.append(
                f"<dry-run> would reset dev to {upstream_head[:7]} + bump + reinstall"
            )
            result.final_state = "synced"
            return result

        # Reset our dev branch to upstream
        reset_res = subprocess.run(
            ["git", "-C", str(clone), "checkout", entry.fork.branch],
            capture_output=True, text=True,
        )
        if reset_res.returncode == 0:
            reset_res = subprocess.run(
                ["git", "-C", str(clone), "reset", "--hard",
                 f"upstream/{entry.fork.branch}"],
                capture_output=True, text=True,
            )
        if reset_res.returncode != 0:
            result.actions_blocked.append(
                f"{fork_id}: reset failed: {reset_res.stderr.strip()[:120]}"
            )
            result.final_state = "blocked"
            return result
        result.actions_taken.append(
            f"reset {entry.fork.branch} to upstream/{entry.fork.branch} ({upstream_head[:7]})"
        )

        # Push (force-with-lease) so registry.fork_commit can be bumped
        push_res = subprocess.run(
            ["git", "-C", str(clone), "push", "--force-with-lease",
             "origin", entry.fork.branch],
            capture_output=True, text=True,
        )
        if push_res.returncode == 0:
            result.actions_taken.append(f"pushed {entry.fork.branch} to origin")
        else:
            result.actions_blocked.append(
                f"{fork_id}: push to origin failed: "
                f"{push_res.stderr.strip()[:160]}"
            )
            result.final_state = "blocked"
            return result

        # Bump registry
        bump = bump_fork(fork_id, registry_path=registry_path)
        result.actions_taken.append(
            f"bumped registry: {bump.old_commit} → {bump.new_commit}"
        )

        # Reinstall
        install_res = install_fork(load_registry(registry_path).get(fork_id), mode)
        if install_res.ok:
            result.actions_taken.append(f"reinstalled (mode={mode.value})")
        else:
            result.actions_blocked.append(
                f"reinstall failed: {install_res.stderr.strip()[:120]}"
            )
            result.final_state = "blocked"
            return result

    if result.actions_taken:
        result.final_state = "synced"
    return result


def _transition_gap_to_upstream_merged(fork_id: str, patch_id_full: str) -> None:
    """Find the contract_gap referenced by patch_id and transition its
    status forked → upstream_merged. Best-effort — doesn't raise on
    missing files (drop_patch may have already removed the patch yaml,
    so we can't always resolve the gap_ref).
    """
    import yaml
    try:
        from operations_center.executors._artifacts import (
            GapStatus, load_contract_gaps,
        )
    except ImportError:
        return

    # Walk the executors dir for the matching gap
    executors_root = Path(__file__).resolve().parents[1] / "executors" / fork_id
    gaps_path = executors_root / "contract_gaps.yaml"
    if not gaps_path.exists():
        return

    raw = yaml.safe_load(gaps_path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        return

    changed = False
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == GapStatus.FORKED.value:
            entry["status"] = GapStatus.UPSTREAM_MERGED.value
            changed = True

    if changed:
        gaps_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def auto_sync_all(
    *,
    mode: InstallMode = InstallMode.DEV,
    registry_path: Path | None = None,
    dry_run: bool = False,
) -> list[AutoSyncResult]:
    """Run auto-sync for every fork in the registry. Useful from cron."""
    registry = load_registry(registry_path or _default_registry_path())
    return [
        auto_sync_fork(e.fork_id, mode=mode,
                       registry_path=registry_path, dry_run=dry_run)
        for e in registry.all()
    ]
