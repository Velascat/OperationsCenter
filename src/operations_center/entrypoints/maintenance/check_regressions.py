# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Detect post-merge regressions and (optionally) open revert PRs.

Wraps `operations_center.post_merge_regression.detect_post_merge_regressions`
into an operator-runnable CLI. Emits a JSON report; with `--revert` set,
also creates revert branches for each detected regression (you push and
open PRs by hand — the CLI never auto-pushes).

    python -m operations_center.entrypoints.maintenance.check_regressions \\
        --config config/operations_center.local.yaml \\
        [--lookback-hours 24] \\
        [--revert] \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.github_pr import GitHubPRClient
from operations_center.config import load_settings
from operations_center.post_merge_regression import (
    create_revert_branch,
    detect_post_merge_regressions,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for post-merge regressions")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--revert", action="store_true",
                        help="create local revert branches for each regression "
                             "(does not push or open PRs)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings(args.config)
    token = settings.git_token()
    if not token:
        print(json.dumps({"error": "no git token"}))
        return 1
    gh = GitHubPRClient(token)

    now = datetime.now(UTC)
    findings: list[dict] = []
    for repo_key, repo_cfg in (settings.repos or {}).items():
        try:
            owner, repo = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
        except ValueError:
            continue
        signals = detect_post_merge_regressions(
            gh, owner, repo,
            base_branch=repo_cfg.default_branch,
            lookback_hours=args.lookback_hours,
            ignored_checks=tuple(getattr(repo_cfg, "ci_ignored_checks", []) or []),
        )
        for sig in signals:
            entry = {
                "repo_key":       repo_key,
                "owner":          owner,
                "repo":           repo,
                "pr_number":      sig.pr_number,
                "merge_sha":      sig.merge_commit_sha,
                "head_sha":       sig.head_sha,
                "base_branch":    sig.base_branch,
                "merged_at":      sig.merged_at,
                "failed_checks":  list(sig.failed_checks),
            }
            # Optional revert step — only LOCAL git ops, never push/open
            if args.revert and not args.dry_run and getattr(repo_cfg, "local_path", None):
                ws = Path(repo_cfg.local_path)
                branch = create_revert_branch(
                    ws, commit_sha=sig.merge_commit_sha,
                    base_branch=sig.base_branch,
                )
                entry["revert_branch"] = branch
            findings.append(entry)

    out = {
        "scanned_at":     now.isoformat(),
        "lookback_hours": args.lookback_hours,
        "revert_local":   bool(args.revert) and not args.dry_run,
        "findings":       findings,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
