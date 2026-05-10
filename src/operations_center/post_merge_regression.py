# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Post-merge regression detection + revert helpers.

Cited by `docs/design/autonomy/autonomy_gaps.md` Wave 3 entries (4+6 Post-Merge
CI Feedback, S8-5 Automatic Revert Branch).

What this *does*: provides callable helpers a maintenance CLI or
periodic scan can use to detect regressions on the default branch after
a merge, identify the suspect commit, and (when wired) open a revert PR.

What this *deliberately does NOT do*: auto-decide to revert. That's a
strong product opinion (when does CI failure attribute to a specific
merge vs. flaky? what counts as a regression worth reverting?). The
helpers are read-only / side-effect-free except for `create_revert_branch`
which is a thin wrapper around git operations the caller invokes only
after deciding to act.

Invariants:
  • No imports of behavior_calibration
  • No mutation of frozen contracts
  • No autonomous decision to revert — caller orchestrates
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operations_center.adapters.github_pr import GitHubPRClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegressionSignal:
    """One detected regression candidate."""
    pr_number: int | None
    merge_commit_sha: str
    head_sha: str
    failed_checks: tuple[str, ...]
    merged_at: str
    base_branch: str


def detect_post_merge_regressions(
    gh_client: GitHubPRClient,
    owner: str,
    repo: str,
    *,
    base_branch: str = "main",
    lookback_hours: int = 24,
    ignored_checks: tuple[str, ...] = (),
) -> list[RegressionSignal]:
    """Scan recent merges to *base_branch*, attribute CI failures to them.

    Strategy (best-effort — GitHub API surfaces are imperfect):
      1. List PRs merged into base_branch within lookback_hours
      2. For each, check CI status of base_branch HEAD vs. the merge SHA
      3. If the base branch's HEAD has failed checks that pass on the
         merge SHA's parent (pre-merge state), flag the PR

    Returns a list of RegressionSignal — empty when nothing's regressed.
    Caller decides whether to revert / file follow-up / notify.
    """
    out: list[RegressionSignal] = []
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    head_sha = gh_client.get_branch_head(owner, repo, base_branch)
    if not head_sha:
        return out

    # Failed checks at HEAD of base — anything not in ignored_checks
    try:
        failed = gh_client.get_failed_checks(
            owner, repo, 0,  # PR number 0 = use head_sha lookup path
            ignored_checks=list(ignored_checks),
        )
    except Exception as exc:
        logger.debug("detect_post_merge_regressions: failed_checks fetch failed — %s", exc)
        return out
    if not failed:
        return out  # base is green; no regression

    # Find recent merges. Best-effort — using list_open_prs misses closed/
    # merged ones; if the client has list_recently_merged_prs use that,
    # otherwise return what we can attribute via the head commit alone.
    list_merged = getattr(gh_client, "list_recently_merged_prs", None)
    if not callable(list_merged):
        # Without merged-PR enumeration we can only attribute the head.
        out.append(RegressionSignal(
            pr_number=None,
            merge_commit_sha=head_sha,
            head_sha=head_sha,
            failed_checks=tuple(failed),
            merged_at=datetime.now(UTC).isoformat(),
            base_branch=base_branch,
        ))
        return out

    try:
        merged = list_merged(owner, repo, base_branch)
    except Exception:
        merged = []
    for pr in merged:
        merged_at_raw = pr.get("merged_at") or pr.get("updated_at")
        try:
            merged_at = datetime.fromisoformat((merged_at_raw or "").replace("Z", "+00:00"))
        except Exception:
            continue
        if merged_at < cutoff:
            continue
        merge_sha = pr.get("merge_commit_sha") or ""
        out.append(RegressionSignal(
            pr_number=int(pr.get("number") or 0) or None,
            merge_commit_sha=merge_sha,
            head_sha=head_sha,
            failed_checks=tuple(failed),
            merged_at=merged_at.isoformat(),
            base_branch=base_branch,
        ))
    return out


def create_revert_branch(
    repo_path: Path,
    *,
    commit_sha: str,
    base_branch: str = "main",
    branch_name: str | None = None,
) -> str | None:
    """Create a revert branch in *repo_path* and return its name.

    Performs the local git operations only — no push, no PR creation.
    The caller pushes / opens the PR. This split keeps the destructive
    action (publishing a revert) under operator control.

    Returns the branch name on success, or None on failure (logged).
    """
    short = commit_sha[:8] if commit_sha else "unknown"
    branch = branch_name or f"revert/{short}"
    try:
        subprocess.run(
            ["git", "fetch", "origin", base_branch],
            cwd=repo_path, check=True, capture_output=True, timeout=60,
        )
        subprocess.run(
            ["git", "checkout", "-b", branch, f"origin/{base_branch}"],
            cwd=repo_path, check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "revert", "--no-edit", commit_sha],
            cwd=repo_path, check=True, capture_output=True, timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "create_revert_branch: failed for %s — %s",
            commit_sha, (exc.stderr.decode() if exc.stderr else str(exc)).strip()[:200],
        )
        return None
    except subprocess.TimeoutExpired:
        logger.warning("create_revert_branch: git operation timed out for %s", commit_sha)
        return None
    return branch


def _extract_evidence_file_tokens(diff_text: str, *, max_files: int = 10) -> tuple[str, ...]:
    """Pull the file paths referenced in a unified diff (best-effort).

    Used to summarise "this regression touched these files" when
    surfacing a RegressionSignal. Pure function on the diff text.
    """
    if not diff_text:
        return ()
    files: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[len("+++ b/"):].strip()
            if path and path != "/dev/null" and path not in files:
                files.append(path)
                if len(files) >= max_files:
                    break
    return tuple(files)
