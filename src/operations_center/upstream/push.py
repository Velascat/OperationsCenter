# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R5 — Auto-PR push.

Pushes a single PATCH-NNN's branch to the fork remote and opens a PR
against upstream via ``gh pr create``. After the push succeeds, rewrites
the patch yaml's ``push_to_upstream`` block so subsequent polls track
that PR.

Opt-in per fork (``auto_pr_push: true`` in registry) and per patch
(``push_to_upstream.enabled: true``). Operator-triggered via
``operations-center-upstream push <patch_id>``; cron jobs may also
trigger it when a ``PUSH_PATCH`` reconcile finding fires (left for the
operator to wire into their cron).

Safety rails:
  - Refuses to push if patch is already marked pushed
  - Refuses if push_to_upstream.enabled is false
  - Refuses if the fork's ``auto_pr_push`` is false
  - Always reports the gh CLI's exit + final PR URL
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from operations_center.upstream.patches import (
    Patch, PatchError, load_patches,
)
from operations_center.upstream.registry import (
    ForkEntry, RegistryError, load_registry, resolve_local_clone,
)


class PushError(RuntimeError):
    """Raised when a push operation cannot proceed."""


@dataclass(frozen=True)
class PushResult:
    fork_id: str
    patch_id: str
    branch: str
    pr_url: Optional[str]
    pushed_branch: bool
    pr_created: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.pushed_branch and self.pr_created


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def push_patch(
    patch_id_full: str,
    *,
    registry_path: Path | None = None,
    patches_root: Path | None = None,
    dry_run: bool = False,
) -> PushResult:
    """Push a single patch to its fork's remote and open an upstream PR.

    ``patch_id_full`` is ``<fork_id>:<PATCH-NNN>``.
    """
    if ":" not in patch_id_full:
        raise PushError(f"patch id must be 'fork:PATCH-NNN' format (got {patch_id_full!r})")
    fork_id, patch_local_id = patch_id_full.split(":", 1)

    registry = load_registry(registry_path)
    try:
        entry = registry.get(fork_id)
    except RegistryError as exc:
        raise PushError(str(exc)) from exc

    patch_reg = load_patches(patches_root)
    patch = patch_reg.get(patch_id_full)
    if patch is None:
        raise PushError(f"patch {patch_id_full!r} not found")

    # Safety checks
    if not entry.auto_pr_push:
        raise PushError(
            f"{fork_id}: registry has auto_pr_push: false — refusing to push"
        )
    if not patch.push_to_upstream.enabled:
        raise PushError(
            f"{patch_id_full}: push_to_upstream.enabled is false — refusing to push"
        )
    if patch.push_to_upstream.pushed:
        raise PushError(
            f"{patch_id_full}: already pushed (pushed_pr_url={patch.push_to_upstream.pushed_pr_url})"
        )

    clone = resolve_local_clone(entry)
    if clone is None:
        raise PushError(f"{fork_id}: no local clone found for push operation")

    # Push the branch to the fork's origin
    push_cmd = ["git", "push", "-u", "origin", patch.fork_branch]
    if dry_run:
        return PushResult(
            fork_id=fork_id, patch_id=patch.id, branch=patch.fork_branch,
            pr_url=None, pushed_branch=True, pr_created=True,
            detail=f"<dry-run> would: {' '.join(shlex.quote(c) for c in push_cmd)}",
        )

    rc, _stdout, stderr = _run(push_cmd, cwd=clone)
    if rc != 0:
        return PushResult(
            fork_id=fork_id, patch_id=patch.id, branch=patch.fork_branch,
            pr_url=None, pushed_branch=False, pr_created=False,
            detail=f"git push failed: {stderr.strip()[:200]}",
        )

    # Open upstream PR via gh
    pr_body = _build_pr_body(patch, fork_id)
    pr_cmd = [
        "gh", "pr", "create",
        "--repo", entry.upstream.repo,
        "--head", f"{entry.fork.repo.split('/', 1)[0]}:{patch.fork_branch}",
        "--base", entry.fork.branch,
        "--title", patch.title,
        "--body", pr_body,
    ]
    rc, stdout, stderr = _run(pr_cmd, cwd=clone)
    if rc != 0:
        return PushResult(
            fork_id=fork_id, patch_id=patch.id, branch=patch.fork_branch,
            pr_url=None, pushed_branch=True, pr_created=False,
            detail=f"gh pr create failed: {stderr.strip()[:200]}",
        )

    pr_url = stdout.strip().splitlines()[-1] if stdout.strip() else None

    # Rewrite the patch yaml to record the push
    if pr_url and not dry_run:
        _record_push(patches_root, fork_id, patch.id, pr_url)

    return PushResult(
        fork_id=fork_id, patch_id=patch.id, branch=patch.fork_branch,
        pr_url=pr_url, pushed_branch=True, pr_created=True,
    )


def _build_pr_body(patch: Patch, fork_id: str) -> str:
    parts = [
        f"Closes contract gap **{patch.contract_gap_ref}**.",
        "",
        f"Fork branch: `{patch.fork_branch}`",
        f"Fork dev commit: `{patch.fork_dev_commit}`",
        "",
        f"Auto-pushed by OperationsCenter Phase 14 (auto_pr_push) for "
        f"`{fork_id}:{patch.id}`.",
    ]
    if patch.upstream.related_pr:
        parts.extend([
            "",
            f"Parallel to existing upstream {patch.upstream.related_pr}.",
        ])
    return "\n".join(parts)


_DEFAULT_PATCHES_ROOT = Path(__file__).parent / "patches"


def _record_push(patches_root: Path | None, fork_id: str, patch_id: str, pr_url: str) -> None:
    root = patches_root or _DEFAULT_PATCHES_ROOT
    target = root / fork_id / f"{patch_id}.yaml"
    if not target.exists():
        return
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    push_block = raw.get("push_to_upstream") or {}
    push_block["pushed"] = True
    push_block["pushed_pr_url"] = pr_url
    raw["push_to_upstream"] = push_block
    target.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def drop_patch(
    patch_id_full: str,
    *,
    patches_root: Path | None = None,
) -> None:
    """Mark a patch as dropped (after upstream merge). Removes the yaml file.

    The patch's contract_gap entry should be transitioned separately
    (forked → upstream_merged → mitigated → closed).
    """
    if ":" not in patch_id_full:
        raise PushError(f"patch id must be 'fork:PATCH-NNN' format")
    fork_id, patch_local_id = patch_id_full.split(":", 1)
    root = patches_root or _DEFAULT_PATCHES_ROOT
    target = root / fork_id / f"{patch_local_id}.yaml"
    if not target.exists():
        raise PushError(f"patch file {target} not found")
    target.unlink()
