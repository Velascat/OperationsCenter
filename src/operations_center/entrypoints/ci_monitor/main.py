# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""CI Monitor: scans open PRs for failing checks and creates fix_pr tasks on the Plane board.

Run as a standalone watcher:
    python -m operations_center.entrypoints.ci_monitor.main --config config/operations_center.local.yaml --watch

One-shot:
    python -m operations_center.entrypoints.ci_monitor.main --config config/operations_center.local.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.github_pr import GitHubPRClient
from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings

# Re-use the same branch pattern as the reviewer.
_BRANCH_TASK_ID_RE = re.compile(
    r"^plane/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)

CI_FIX_STATE_DIR = Path("state/ci_fixes")
PR_REVIEW_STATE_DIR = Path("state/pr_reviews")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _ci_fix_state_path(owner: str, repo: str, pr_number: int) -> Path:
    return CI_FIX_STATE_DIR / f"{owner}_{repo}_{pr_number}.json"


def _load_ci_fix_state(owner: str, repo: str, pr_number: int) -> dict | None:
    path = _ci_fix_state_path(owner, repo, pr_number)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _save_ci_fix_state(owner: str, repo: str, pr_number: int, state: dict) -> None:
    CI_FIX_STATE_DIR.mkdir(parents=True, exist_ok=True)
    _ci_fix_state_path(owner, repo, pr_number).write_text(json.dumps(state, indent=2))


def _pr_is_awaiting_ci(branch: str) -> bool:
    """Return True if the reviewer is already handling CI for this PR in awaiting_ci phase."""
    m = _BRANCH_TASK_ID_RE.match(branch)
    if not m:
        return False
    task_id = m.group(1)
    state_file = PR_REVIEW_STATE_DIR / f"{task_id}.json"
    if not state_file.exists():
        return False
    try:
        state = json.loads(state_file.read_text())
        return state.get("phase") == "awaiting_ci"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Task description builder
# ---------------------------------------------------------------------------

def _build_fix_pr_description(
    *,
    repo_key: str,
    branch: str,
    base_branch: str,
    pr_number: int,
    pr_url: str,
    failures: list[str],
) -> str:
    failures_text = "\n".join(f"- {f}" for f in failures)
    lines = [
        "## Execution",
        f"repo: {repo_key}",
        "mode: fix_pr",
        f"base_branch: {branch}",
        "",
        "## Goal",
        f"Fix the following CI check failures on PR #{pr_number} (branch `{branch}`):",
        "",
        failures_text,
        "",
        "Reproduce each failure locally, identify the root cause, and fix it.",
        "Do not change the intent of the existing changes on this branch.",
        "",
        "## Context",
        f"- pr_url: {pr_url}",
        f"- pr_branch: {branch}",
        f"- fix_pr_base: {base_branch}",
    ]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Monitor cycle
# ---------------------------------------------------------------------------

def run_ci_monitor_cycle(
    plane_client: PlaneClient,
    settings,
    logger: logging.Logger,
) -> int:
    """Scan open PRs for CI failures and create fix_pr tasks. Returns count of new tasks."""
    created = 0

    for repo_key, repo_cfg in settings.repos.items():
        if not getattr(repo_cfg, "await_review", False):
            continue
        token = settings.repo_git_token(repo_key)
        if not token:
            continue

        try:
            owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
        except ValueError:
            continue

        gh = GitHubPRClient(token)
        try:
            open_prs = gh.list_open_prs(owner, repo_name)
        except Exception as exc:
            logger.warning(json.dumps({
                "event": "ci_monitor_list_prs_failed",
                "repo_key": repo_key,
                "error": str(exc),
            }))
            continue

        for pr in open_prs:
            branch = pr.get("head", {}).get("ref", "")
            if not _BRANCH_TASK_ID_RE.match(branch):
                continue  # Only monitor branches we created.

            pr_number = pr["number"]
            pr_url = pr.get("html_url", "")

            # Skip if the reviewer is already autonomously fixing CI for this PR.
            if _pr_is_awaiting_ci(branch):
                logger.info(json.dumps({
                    "event": "ci_monitor_skipped_awaiting_ci",
                    "pr_number": pr_number,
                    "repo_key": repo_key,
                    "branch": branch,
                }))
                continue

            try:
                pr_data = gh.get_pr(owner, repo_name, pr_number)
                if pr_data.get("mergeable_state") != "unstable":
                    continue  # CI not currently failing.

                failures = gh.get_failed_checks(owner, repo_name, pr_number, pr_data=pr_data)
                if not failures:
                    continue  # Checks haven't finished yet or no identifiable failures.

                head_sha = (pr_data.get("head") or {}).get("sha", "")

                # Deduplicate: skip if we already created a fix task for this commit.
                existing = _load_ci_fix_state(owner, repo_name, pr_number)
                if existing and existing.get("head_sha") == head_sha:
                    logger.info(json.dumps({
                        "event": "ci_monitor_skipped_already_tracked",
                        "pr_number": pr_number,
                        "repo_key": repo_key,
                        "fix_task_id": existing.get("fix_task_id"),
                    }))
                    continue

                description = _build_fix_pr_description(
                    repo_key=repo_key,
                    branch=branch,
                    base_branch=repo_cfg.default_branch,
                    pr_number=pr_number,
                    pr_url=pr_url,
                    failures=failures,
                )
                # Keep title short: show first two check names.
                check_names = [f.split(":")[0].strip() for f in failures[:2]]
                title = f"Fix CI: {', '.join(check_names)} on PR #{pr_number}"

                created_issue = plane_client.create_issue(
                    name=title,
                    description=description,
                    state="Ready for AI",
                    label_names=["task-kind: fix_pr", f"repo: {repo_key}", "source: ci-monitor"],
                )
                fix_task_id = str(created_issue.get("id", ""))

                _save_ci_fix_state(owner, repo_name, pr_number, {
                    "owner": owner,
                    "repo": repo_name,
                    "pr_number": pr_number,
                    "branch": branch,
                    "head_sha": head_sha,
                    "fix_task_id": fix_task_id,
                    "failures": failures,
                    "created_at": datetime.now(UTC).isoformat(),
                })

                logger.info(json.dumps({
                    "event": "ci_fix_task_created",
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "repo_key": repo_key,
                    "fix_task_id": fix_task_id,
                    "failures": failures,
                }))
                created += 1

            except Exception as exc:
                logger.warning(json.dumps({
                    "event": "ci_monitor_pr_error",
                    "pr_number": pr_number,
                    "repo_key": repo_key,
                    "error": str(exc),
                }))

    return created


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------

def run_monitor_loop(
    plane_client: PlaneClient,
    settings,
    *,
    poll_interval_seconds: int,
    max_cycles: int | None,
    status_dir: Path | None,
) -> None:
    logger = logging.getLogger(__name__)
    cycle = 0

    while True:
        cycle += 1
        logger.info(json.dumps({"event": "ci_monitor_cycle_start", "cycle": cycle}))
        try:
            created = run_ci_monitor_cycle(plane_client, settings, logger)
            logger.info(json.dumps({"event": "ci_monitor_cycle_end", "cycle": cycle, "tasks_created": created}))

            if status_dir:
                status_dir.mkdir(parents=True, exist_ok=True)
                (status_dir / "ci_monitor.status.json").write_text(json.dumps({
                    "cycle": cycle,
                    "tasks_created": created,
                    "updated_at": datetime.now(UTC).isoformat(),
                }, indent=2))

        except Exception as exc:
            logger.warning(json.dumps({"event": "ci_monitor_cycle_error", "cycle": cycle, "error": str(exc)}))

        if max_cycles is not None and cycle >= max_cycles:
            logger.info(json.dumps({"event": "ci_monitor_complete", "cycles": cycle}))
            return

        time.sleep(poll_interval_seconds)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor open PRs for CI failures and create fix_pr tasks")
    parser.add_argument("--config", required=True)
    parser.add_argument("--watch", action="store_true", help="Run in watch mode (poll continuously)")
    parser.add_argument("--poll-interval-seconds", type=int, default=120)
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--status-dir")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings(args.config)

    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    logger = logging.getLogger(__name__)

    try:
        if args.watch:
            run_monitor_loop(
                client,
                settings,
                poll_interval_seconds=args.poll_interval_seconds,
                max_cycles=args.max_cycles,
                status_dir=Path(args.status_dir) if args.status_dir else None,
            )
        else:
            created = run_ci_monitor_cycle(client, settings, logger)
            print(f"CI monitor: {created} fix task(s) created")
    finally:
        client.close()


if __name__ == "__main__":
    main()
