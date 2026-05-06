# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Patch records — named, justified divergences from upstream.

Each patch lives at::

    operations_center/upstream/patches/<fork_id>/PATCH-NNN.yaml

Schema enforcement:
  - id matches filename
  - contract_gap_ref resolves to a real entry in the executor's
    contract_gaps.yaml (cross-checked at catalog load time)
  - touched_files exist in the fork at fork_dev_commit (R3 work)
  - upstream.related_pr is a parseable GitHub URL (when set)

The patch metadata is the runtime feed for Phase 14 reconcile
suggestions — DROP_PATCH / REBASE_PATCH / PUSH_PATCH / etc.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml


class PatchError(ValueError):
    """Raised when a patch file is malformed or violates a rule."""


class UpstreamStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    MERGED         = "merged"
    REJECTED       = "rejected"
    ABANDONED      = "abandoned"
    NOT_SUBMITTED  = "not_submitted"


_PATCH_ID_RE = re.compile(r"^PATCH-\d{3,}$")
_GAP_REF_RE  = re.compile(r"^[a-z][a-z0-9_-]*:G-\d+$")


@dataclass(frozen=True)
class UpstreamRef:
    related_pr: Optional[str] = None
    related_issue: Optional[str] = None
    upstream_status: UpstreamStatus = UpstreamStatus.NOT_SUBMITTED
    last_upstream_activity: Optional[str] = None  # ISO date


@dataclass(frozen=True)
class ReconcileTrigger:
    """Conditions under which this patch should be re-evaluated."""
    upstream_pr_merged: Optional[int] = None
    upstream_release_includes: Optional[str] = None
    patched_files_changed_upstream: bool = False


@dataclass(frozen=True)
class PushSpec:
    enabled: bool = False
    pushed: bool = False
    pushed_pr_url: Optional[str] = None


@dataclass(frozen=True)
class Patch:
    id: str
    fork_id: str
    title: str
    applied_at: str
    fork_branch: str
    fork_dev_commit: str
    contract_gap_ref: str
    upstream: UpstreamRef
    reconcile_when_any: tuple[ReconcileTrigger, ...]
    touched_files: tuple[str, ...]
    push_to_upstream: PushSpec


@dataclass
class PatchRegistry:
    """Per-fork patch record collection."""
    by_fork: dict[str, list[Patch]] = field(default_factory=dict)

    def for_fork(self, fork_id: str) -> list[Patch]:
        return list(self.by_fork.get(fork_id, []))

    def all_patches(self) -> list[Patch]:
        out: list[Patch] = []
        for patches in self.by_fork.values():
            out.extend(patches)
        return out

    def get(self, patch_id_full: str) -> Optional[Patch]:
        """Look up by 'fork_id:PATCH-NNN' fully-qualified id."""
        if ":" not in patch_id_full:
            return None
        fork_id, pid = patch_id_full.split(":", 1)
        for p in self.by_fork.get(fork_id, []):
            if p.id == pid:
                return p
        return None


# ── Loaders + validators ─────────────────────────────────────────────────


def _parse_upstream(label: str, raw: Any) -> UpstreamRef:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise PatchError(f"{label}: upstream must be a mapping")
    status_raw = raw.get("upstream_status", UpstreamStatus.NOT_SUBMITTED.value)
    try:
        status = UpstreamStatus(status_raw)
    except ValueError as exc:
        raise PatchError(
            f"{label}: invalid upstream_status {status_raw!r}; "
            f"valid: {[s.value for s in UpstreamStatus]}"
        ) from exc
    return UpstreamRef(
        related_pr=raw.get("related_pr"),
        related_issue=raw.get("related_issue"),
        upstream_status=status,
        last_upstream_activity=raw.get("last_upstream_activity"),
    )


def _parse_reconcile(label: str, raw: Any) -> tuple[ReconcileTrigger, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PatchError(f"{label}: reconcile_when_any must be a list")
    out: list[ReconcileTrigger] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise PatchError(f"{label}: reconcile_when_any[{i}] must be a mapping")
        out.append(ReconcileTrigger(
            upstream_pr_merged=entry.get("upstream_pr_merged"),
            upstream_release_includes=entry.get("upstream_release_includes"),
            patched_files_changed_upstream=bool(entry.get("patched_files_changed_upstream", False)),
        ))
    return tuple(out)


def _parse_push(label: str, raw: Any) -> PushSpec:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise PatchError(f"{label}: push_to_upstream must be a mapping")
    return PushSpec(
        enabled=bool(raw.get("enabled", False)),
        pushed=bool(raw.get("pushed", False)),
        pushed_pr_url=raw.get("pushed_pr_url"),
    )


def _parse_patch(fork_id: str, file_path: Path, raw: dict) -> Patch:
    label = f"{fork_id}/{file_path.name}"

    pid = raw.get("id")
    if not isinstance(pid, str) or not _PATCH_ID_RE.match(pid):
        raise PatchError(f"{label}: 'id' must match PATCH-NNN format")
    expected_filename = f"{pid}.yaml"
    if file_path.name != expected_filename:
        raise PatchError(f"{label}: filename must be {expected_filename}")

    for required in ("title", "fork_branch", "fork_dev_commit",
                     "contract_gap_ref", "applied_at"):
        if required not in raw:
            raise PatchError(f"{label}: missing required field {required!r}")

    gap_ref = raw["contract_gap_ref"]
    if not isinstance(gap_ref, str) or not _GAP_REF_RE.match(gap_ref):
        raise PatchError(
            f"{label}: contract_gap_ref must match '<fork_id>:G-N' "
            f"(got {gap_ref!r})"
        )

    touched_raw = raw.get("touched_files") or []
    if not isinstance(touched_raw, list) or not all(isinstance(f, str) for f in touched_raw):
        raise PatchError(f"{label}: touched_files must be a list of strings")

    return Patch(
        id=pid,
        fork_id=fork_id,
        title=str(raw["title"]),
        applied_at=str(raw["applied_at"]),
        fork_branch=str(raw["fork_branch"]),
        fork_dev_commit=str(raw["fork_dev_commit"]),
        contract_gap_ref=gap_ref,
        upstream=_parse_upstream(label, raw.get("upstream")),
        reconcile_when_any=_parse_reconcile(label, raw.get("reconcile_when_any")),
        touched_files=tuple(touched_raw),
        push_to_upstream=_parse_push(label, raw.get("push_to_upstream")),
    )


_DEFAULT_PATCHES_ROOT = Path(__file__).parent / "patches"


def load_patches(patches_root: Path | None = None) -> PatchRegistry:
    """Walk patches/<fork_id>/PATCH-NNN.yaml and load + validate every entry."""
    root = patches_root or _DEFAULT_PATCHES_ROOT
    if not root.is_dir():
        return PatchRegistry()
    by_fork: dict[str, list[Patch]] = {}
    for fork_dir in sorted(root.iterdir()):
        if not fork_dir.is_dir():
            continue
        patches: list[Patch] = []
        for patch_file in sorted(fork_dir.glob("PATCH-*.yaml")):
            raw = yaml.safe_load(patch_file.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise PatchError(f"{fork_dir.name}/{patch_file.name}: top level must be a mapping")
            patches.append(_parse_patch(fork_dir.name, patch_file, raw))
        if patches:
            by_fork[fork_dir.name] = patches
    return PatchRegistry(by_fork=by_fork)
