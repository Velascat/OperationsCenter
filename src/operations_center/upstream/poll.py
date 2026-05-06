# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R4 — Upstream poll + reconcile suggestions.

Per registered fork:
  - Read latest_release / latest_commit_sha from upstream
  - For each PATCH-NNN, check upstream PR status + recent commits
    touching ``touched_files``
  - Emit ``UPSTREAM_RECONCILE`` findings:
      DROP_PATCH               — upstream merged the same fix
      REBASE_PATCH             — upstream changed our touched_files
      PUSH_PATCH               — auto_pr_push enabled but not pushed
      STALE_REVIEW             — pushed PR has no review activity for >30d
      REVIEW_REQUEST_ABANDONED — pushed PR closed without merge

The GitHub API client is pluggable (``UpstreamApiClient`` Protocol). The
real implementation uses ``gh`` CLI for unauth'd reads; tests inject a
fake.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol

from operations_center.upstream.patches import (
    Patch, PatchRegistry, UpstreamStatus, load_patches,
)
from operations_center.upstream.registry import (
    ForkEntry, ForkRegistry, load_registry,
)


_STALE_REVIEW_DAYS = 30


class ReconcileSuggestion(str, Enum):
    DROP_PATCH                = "DROP_PATCH"
    REBASE_PATCH              = "REBASE_PATCH"
    PUSH_PATCH                = "PUSH_PATCH"
    STALE_REVIEW              = "STALE_REVIEW"
    REVIEW_REQUEST_ABANDONED  = "REVIEW_REQUEST_ABANDONED"


@dataclass(frozen=True)
class PrSnapshot:
    """Minimal upstream PR state we care about."""
    number: int
    state: str                          # open | closed
    merged: bool
    last_activity_iso: Optional[str]    # ISO date of last PR activity


@dataclass(frozen=True)
class UpstreamSnapshot:
    """A single poll result for one fork."""
    fork_id: str
    upstream_repo: str
    latest_release: Optional[str] = None
    latest_commit_sha: Optional[str] = None
    cited_prs: dict[int, PrSnapshot] = field(default_factory=dict)
    pushed_prs: dict[str, PrSnapshot] = field(default_factory=dict)
    files_changed_since_base: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReconcileFinding:
    """One row of the audit-stream output of a poll."""
    rule: str = "UPSTREAM_RECONCILE"
    patch_id: str = ""             # e.g. "kodo:PATCH-001"
    suggestion: ReconcileSuggestion = ReconcileSuggestion.PUSH_PATCH
    reason: str = ""
    detected_at: str = ""
    action_link: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["suggestion"] = self.suggestion.value
        return d


# ── Pluggable API client ─────────────────────────────────────────────────


class UpstreamApiClient(Protocol):
    def latest_release(self, repo: str) -> Optional[str]: ...
    def latest_commit_sha(self, repo: str, branch: str = "main") -> Optional[str]: ...
    def get_pr(self, repo: str, number: int) -> Optional[PrSnapshot]: ...
    def files_changed_between(self, repo: str, base_sha: str, head_sha: str) -> list[str]: ...


class GhCliClient:
    """Default client — shells out to the ``gh`` CLI for unauth'd reads.

    Each call is bounded; failure (gh not installed, network error, etc.)
    returns None / empty list rather than raising. The poll job continues
    with degraded info.
    """

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self._timeout = timeout_seconds

    def _gh_json(self, *args: str) -> Any:
        cmd = ["gh", *args]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self._timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None

    def latest_release(self, repo: str) -> Optional[str]:
        data = self._gh_json(
            "release", "view", "--repo", repo, "--json", "tagName",
        )
        if isinstance(data, dict):
            tag = data.get("tagName")
            if isinstance(tag, str):
                return tag.lstrip("v")
        return None

    def latest_commit_sha(self, repo: str, branch: str = "main") -> Optional[str]:
        data = self._gh_json(
            "api", f"repos/{repo}/commits/{branch}", "--jq", ".sha",
        )
        if isinstance(data, str) and len(data) >= 7:
            return data
        return None

    def get_pr(self, repo: str, number: int) -> Optional[PrSnapshot]:
        data = self._gh_json(
            "pr", "view", str(number), "--repo", repo,
            "--json", "number,state,mergedAt,updatedAt",
        )
        if not isinstance(data, dict):
            return None
        merged_at = data.get("mergedAt")
        return PrSnapshot(
            number=int(data.get("number") or number),
            state=str(data.get("state") or "").lower(),
            merged=bool(merged_at),
            last_activity_iso=data.get("updatedAt") or merged_at,
        )

    def files_changed_between(self, repo: str, base_sha: str, head_sha: str) -> list[str]:
        data = self._gh_json(
            "api", f"repos/{repo}/compare/{base_sha}...{head_sha}",
            "--jq", "[.files[].filename]",
        )
        if isinstance(data, list):
            return [str(f) for f in data if isinstance(f, str)]
        return []


# ── Poll orchestration ──────────────────────────────────────────────────


def poll_fork(
    entry: ForkEntry,
    patches: list[Patch],
    *,
    client: UpstreamApiClient,
) -> UpstreamSnapshot:
    """Hit the upstream API and assemble a snapshot for one fork."""
    cited_prs: dict[int, PrSnapshot] = {}
    pushed_prs: dict[str, PrSnapshot] = {}
    for p in patches:
        # The PR number cited in patch.upstream.related_pr (e.g. ".../pull/49")
        if p.upstream.related_pr:
            num = _extract_pr_number(p.upstream.related_pr)
            if num is not None and num not in cited_prs:
                snap = client.get_pr(entry.upstream.repo, num)
                if snap is not None:
                    cited_prs[num] = snap
        # The PR we pushed ourselves (via auto_pr_push)
        if p.push_to_upstream.pushed and p.push_to_upstream.pushed_pr_url:
            num = _extract_pr_number(p.push_to_upstream.pushed_pr_url)
            if num is not None:
                snap = client.get_pr(entry.upstream.repo, num)
                if snap is not None:
                    pushed_prs[p.id] = snap

    files_changed: list[str] = []
    if entry.upstream.latest_commit_sha and entry.base_commit:
        upstream_head = client.latest_commit_sha(entry.upstream.repo, entry.fork.branch)
        if upstream_head and upstream_head != entry.base_commit:
            files_changed = client.files_changed_between(
                entry.upstream.repo, entry.base_commit, upstream_head,
            )

    return UpstreamSnapshot(
        fork_id=entry.fork_id,
        upstream_repo=entry.upstream.repo,
        latest_release=client.latest_release(entry.upstream.repo),
        latest_commit_sha=client.latest_commit_sha(entry.upstream.repo, entry.fork.branch),
        cited_prs=cited_prs,
        pushed_prs=pushed_prs,
        files_changed_since_base=files_changed,
    )


def reconcile(
    entry: ForkEntry,
    patches: list[Patch],
    snapshot: UpstreamSnapshot,
    *,
    today: Optional[date] = None,
) -> list[ReconcileFinding]:
    """Compare the patches' state against the snapshot; emit findings."""
    today = today or date.today()
    out: list[ReconcileFinding] = []
    detected = today.isoformat()

    for p in patches:
        full_id = f"{entry.fork_id}:{p.id}"

        # 1. DROP_PATCH — any reconcile_when_any condition met
        for trigger in p.reconcile_when_any:
            if trigger.upstream_pr_merged is not None:
                snap = snapshot.cited_prs.get(trigger.upstream_pr_merged)
                if snap and snap.merged:
                    out.append(ReconcileFinding(
                        patch_id=full_id,
                        suggestion=ReconcileSuggestion.DROP_PATCH,
                        reason=(
                            f"upstream PR #{trigger.upstream_pr_merged} merged "
                            f"({snap.last_activity_iso or 'date unknown'})"
                        ),
                        detected_at=detected,
                        action_link=p.upstream.related_pr,
                    ))
                    break  # one DROP_PATCH per patch is enough
            if trigger.upstream_release_includes is not None:
                if snapshot.latest_release == trigger.upstream_release_includes:
                    out.append(ReconcileFinding(
                        patch_id=full_id,
                        suggestion=ReconcileSuggestion.DROP_PATCH,
                        reason=f"upstream released {trigger.upstream_release_includes}",
                        detected_at=detected,
                    ))
                    break

        # 2. REBASE_PATCH — touched_files changed upstream
        if any(f in snapshot.files_changed_since_base for f in p.touched_files):
            overlapping = sorted(
                set(p.touched_files) & set(snapshot.files_changed_since_base)
            )
            out.append(ReconcileFinding(
                patch_id=full_id,
                suggestion=ReconcileSuggestion.REBASE_PATCH,
                reason=f"upstream changed touched files: {overlapping}",
                detected_at=detected,
            ))

        # 3. PUSH_PATCH — auto_pr_push enabled but unpushed
        if (
            entry.auto_pr_push
            and p.push_to_upstream.enabled
            and not p.push_to_upstream.pushed
        ):
            out.append(ReconcileFinding(
                patch_id=full_id,
                suggestion=ReconcileSuggestion.PUSH_PATCH,
                reason="auto_pr_push enabled but pushed_pr_url is unset",
                detected_at=detected,
            ))

        # 4. STALE_REVIEW / REVIEW_REQUEST_ABANDONED — for our pushed PRs
        pushed = snapshot.pushed_prs.get(p.id)
        if pushed:
            if pushed.state == "closed" and not pushed.merged:
                out.append(ReconcileFinding(
                    patch_id=full_id,
                    suggestion=ReconcileSuggestion.REVIEW_REQUEST_ABANDONED,
                    reason="our pushed PR closed without merge",
                    detected_at=detected,
                    action_link=p.push_to_upstream.pushed_pr_url,
                ))
            elif pushed.state == "open" and pushed.last_activity_iso:
                last = _parse_iso_date(pushed.last_activity_iso)
                if last and (today - last) > timedelta(days=_STALE_REVIEW_DAYS):
                    out.append(ReconcileFinding(
                        patch_id=full_id,
                        suggestion=ReconcileSuggestion.STALE_REVIEW,
                        reason=(
                            f"our pushed PR has no activity since {last.isoformat()} "
                            f"(>{_STALE_REVIEW_DAYS} days)"
                        ),
                        detected_at=detected,
                        action_link=p.push_to_upstream.pushed_pr_url,
                    ))

    return out


def poll_all(
    *,
    registry_path: Path | None = None,
    patches_root: Path | None = None,
    client: Optional[UpstreamApiClient] = None,
    today: Optional[date] = None,
) -> list[ReconcileFinding]:
    """Run the full poll+reconcile pass. Returns flat list of findings."""
    registry = load_registry(registry_path)
    patch_reg = load_patches(patches_root)
    api = client or GhCliClient()

    findings: list[ReconcileFinding] = []
    for entry in registry.all():
        patches = patch_reg.for_fork(entry.fork_id)
        snapshot = poll_fork(entry, patches, client=api)
        findings.extend(reconcile(entry, patches, snapshot, today=today))
    return findings


# ── Helpers ─────────────────────────────────────────────────────────────


def _extract_pr_number(url_or_number: str) -> Optional[int]:
    """Parse a PR number from a github URL or a bare integer-string."""
    if url_or_number.isdigit():
        return int(url_or_number)
    if "/pull/" in url_or_number:
        tail = url_or_number.split("/pull/", 1)[1]
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            return int(digits)
    return None


def _parse_iso_date(value: str) -> Optional[date]:
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None
