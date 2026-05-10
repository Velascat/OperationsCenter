# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Close PRs that have been open longer than RepoSettings.stale_pr_days.

Wires the previously-dead `stale_pr_days` field. For each repo with the
field set (default 7), scans GitHub open PRs and closes any whose
``updated_at`` is older than the threshold. The associated branch is left
on origin so the operator can inspect / re-open if desired; only the PR
is closed. A comment is left on the PR explaining why.

Skips PRs that:
  • have a `do-not-close` label (case-insensitive)
  • are not on a head branch matching the autonomy prefixes (goal/, test/,
    improve/, plane/) — manual PRs are out of scope

    python -m operations_center.entrypoints.maintenance.close_stale_prs \\
        --config config/operations_center.local.yaml [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.github_pr import GitHubPRClient
from operations_center.config import load_settings


_AUTONOMY_PREFIXES = ("goal/", "test/", "improve/", "plane/")
_PROTECT_LABEL = "do-not-close"


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Close stale autonomy PRs")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings(args.config)
    token = settings.git_token()
    if not token:
        print(json.dumps({"error": "no git token configured"}, ensure_ascii=False))
        return 1
    gh = GitHubPRClient(token)

    now = datetime.now(UTC)
    closed: list[dict] = []
    skipped: list[dict] = []

    for repo_key, repo_cfg in (settings.repos or {}).items():
        threshold_days = int(getattr(repo_cfg, "stale_pr_days", 0) or 0)
        if threshold_days <= 0:
            continue
        try:
            owner, repo = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
        except ValueError:
            skipped.append({"repo_key": repo_key, "reason": "unparseable_clone_url"})
            continue
        try:
            prs = gh.list_open_prs(owner, repo)
        except Exception as exc:
            skipped.append({"repo_key": repo_key, "reason": f"list_failed: {exc}"})
            continue
        for pr in prs:
            head_ref = ((pr.get("head") or {}).get("ref") or "").lower()
            if not head_ref.startswith(_AUTONOMY_PREFIXES):
                continue  # manual PR — don't touch
            labels_lower = [
                (lab.get("name") if isinstance(lab, dict) else str(lab) or "").strip().lower()
                for lab in (pr.get("labels") or [])
            ]
            if _PROTECT_LABEL in labels_lower:
                skipped.append({
                    "repo_key": repo_key, "pr": pr.get("number"),
                    "reason": "do_not_close_label",
                })
                continue
            ts = _parse_iso(pr.get("updated_at")) or _parse_iso(pr.get("created_at"))
            if ts is None:
                continue
            age_days = (now - ts).total_seconds() / 86400
            if age_days < threshold_days:
                continue
            entry = {
                "repo_key":  repo_key,
                "pr":        pr.get("number"),
                "branch":    head_ref,
                "age_days":  round(age_days, 1),
                "threshold": threshold_days,
                "url":       pr.get("html_url"),
            }
            if args.dry_run:
                entry["action"] = "would_close"
                closed.append(entry)
                continue
            try:
                gh.post_comment(
                    owner, repo, pr["number"],
                    f"Auto-closing — PR has been open {round(age_days, 1)}d, "
                    f"threshold is {threshold_days}d (RepoSettings.stale_pr_days). "
                    f"Branch `{head_ref}` is preserved on origin if you want to re-open.",
                )
                gh.close_pr(owner, repo, pr["number"])
                entry["action"] = "closed"
                closed.append(entry)
            except Exception as exc:
                entry["action"] = "error"
                entry["error"]  = str(exc)
                skipped.append(entry)

    out = {
        "scanned_at":          now.isoformat(),
        "dry_run":             args.dry_run,
        "closed_count":        sum(1 for c in closed if c.get("action") == "closed"),
        "would_close_count":   sum(1 for c in closed if c.get("action") == "would_close"),
        "skipped_count":       len(skipped),
        "closed":              closed,
        "skipped":             skipped[:20],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
