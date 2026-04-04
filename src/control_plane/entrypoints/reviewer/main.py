from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from control_plane.adapters.github_pr import GitHubPRClient
from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService
from control_plane.config import load_settings

# Matches plane/{uuid}-{slug} branch names — uuid portion is the Plane task_id
_BRANCH_TASK_ID_RE = re.compile(
    r"^plane/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)

PR_REVIEW_STATE_DIR = Path("state/pr_reviews")
REVIEW_TIMEOUT_SECONDS = 86400  # 1 day
MAX_REVISION_LOOPS = 3


def _load_pr_states() -> list[tuple[Path, dict]]:
    if not PR_REVIEW_STATE_DIR.exists():
        return []
    states = []
    for f in sorted(PR_REVIEW_STATE_DIR.glob("*.json")):
        try:
            states.append((f, json.loads(f.read_text())))
        except Exception:
            pass
    return states


def _merge_and_finalize(
    gh: GitHubPRClient,
    state: dict,
    state_file: Path,
    plane_client: PlaneClient,
    logger: logging.Logger,
    *,
    reason: str,
) -> None:
    owner = state["owner"]
    repo = state["repo"]
    pr_number = state["pr_number"]
    branch = state["branch"]
    task_id = state["task_id"]
    pr_url = state["pr_url"]

    try:
        gh.merge_pr(owner, repo, pr_number)
        logger.info(json.dumps({"event": "pr_merged", "task_id": task_id, "pr_number": pr_number, "reason": reason}))
    except Exception as exc:
        logger.warning(json.dumps({"event": "pr_merge_failed", "task_id": task_id, "error": str(exc)}))
        return

    try:
        gh.delete_branch(owner, repo, branch)
    except Exception as exc:
        logger.warning(json.dumps({"event": "branch_delete_failed", "branch": branch, "error": str(exc)}))

    try:
        plane_client.transition_issue(task_id, "Done")
        plane_client.comment_issue(
            task_id,
            f"[Review] PR merged ({reason})\n- pr_url: {pr_url}\n- branch: {branch}",
        )
    except Exception as exc:
        logger.warning(json.dumps({"event": "plane_update_failed", "task_id": task_id, "error": str(exc)}))

    state_file.unlink(missing_ok=True)
    logger.info(json.dumps({"event": "pr_state_removed", "task_id": task_id, "state_file": str(state_file)}))


def _process_pr_state(
    state_file: Path,
    state: dict,
    plane_client: PlaneClient,
    service: ExecutionService,
    logger: logging.Logger,
) -> int:
    """Process a single PR review state. Returns 1 if an action was taken, 0 otherwise."""
    repo_key = state["repo_key"]
    token = service.settings.repo_git_token(repo_key)
    if not token:
        logger.warning(json.dumps({"event": "pr_review_no_token", "repo_key": repo_key}))
        return 0

    gh = GitHubPRClient(token)
    owner = state["owner"]
    repo = state["repo"]
    pr_number = state["pr_number"]
    task_id = state["task_id"]

    # Timeout: merge after 1 day without approval
    created_at = datetime.fromisoformat(state["created_at"])
    elapsed = (datetime.now(UTC) - created_at).total_seconds()
    if elapsed > REVIEW_TIMEOUT_SECONDS:
        logger.info(json.dumps({"event": "pr_review_timeout", "task_id": task_id, "elapsed_hours": round(elapsed / 3600, 1)}))
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="timeout")
        return 1

    # 👍 on the PR itself → merge
    pr_reactions = gh.get_pr_reactions(owner, repo, pr_number)
    if gh.has_thumbs_up(pr_reactions):
        logger.info(json.dumps({"event": "pr_approved", "task_id": task_id}))
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="approved")
        return 1

    # 👍 on the last bot comment → merge
    last_bot_comment_id = state.get("last_bot_comment_id")
    if last_bot_comment_id:
        comment_reactions = gh.get_comment_reactions(owner, repo, last_bot_comment_id)
        if gh.has_thumbs_up(comment_reactions):
            logger.info(json.dumps({"event": "pr_comment_approved", "task_id": task_id, "comment_id": last_bot_comment_id}))
            _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="comment_approved")
            return 1

    # Check for new comments to trigger a revision (both conversation and inline review comments)
    all_comments = gh.list_pr_comments(owner, repo, pr_number)
    try:
        review_comments = gh.list_pr_review_comments(owner, repo, pr_number)
        # Tag review comments so we can distinguish them when building the revision prompt
        for rc in review_comments:
            rc.setdefault("_source", "review")
        all_comments = all_comments + review_comments
    except Exception as exc:
        logger.warning(json.dumps({"event": "pr_review_comments_failed", "task_id": task_id, "error": str(exc)}))

    bot_comment_ids: set[int] = set(state.get("bot_comment_ids", []))
    processed_human_ids: set[int] = set(state.get("processed_human_comment_ids", []))
    new_human_comments = [
        c for c in all_comments
        if c["id"] not in bot_comment_ids and c["id"] not in processed_human_ids
    ]

    if not new_human_comments:
        # 👀 on the PR and no comments yet → reviewer bot is still looking, wait
        if gh.has_eyes(pr_reactions):
            logger.info(json.dumps({"event": "pr_review_in_progress", "task_id": task_id, "reason": "eyes_reaction_present"}))
        return 0

    latest_comment = new_human_comments[-1]
    latest_id = latest_comment["id"]
    loop_count = state.get("loop_count", 0)

    # Hit max loops → merge with notice
    if loop_count >= MAX_REVISION_LOOPS:
        logger.info(json.dumps({"event": "pr_max_loops_reached", "task_id": task_id, "loop_count": loop_count}))
        try:
            notice = gh.post_comment(owner, repo, pr_number, "Maximum revision loops (3) reached — merging as-is.")
            bot_comment_ids.add(notice["id"])
        except Exception:
            pass
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="max_loops")
        return 1

    # Run kodo revision pass — include file/line context for inline review comments
    review_comment = latest_comment["body"]
    if latest_comment.get("_source") == "review" and latest_comment.get("path"):
        review_comment = f"[{latest_comment['path']}]\n{review_comment}"
    repo_cfg = service.settings.repos[repo_key]

    logger.info(json.dumps({
        "event": "pr_revision_start",
        "task_id": task_id,
        "loop_count": loop_count,
        "comment_id": latest_id,
    }))

    success, changed_files = service.run_review_pass(
        repo_key=repo_key,
        clone_url=repo_cfg.clone_url,
        branch=state["branch"],
        base_branch=state["base"],
        original_goal=state.get("original_goal", ""),
        review_comment=review_comment,
        task_id=task_id,
    )

    logger.info(json.dumps({
        "event": "pr_revision_end",
        "task_id": task_id,
        "success": success,
        "changed_files": len(changed_files),
    }))

    if success:
        reply = "Revision applied. React with 👍 to merge, or leave another comment for further changes."
    else:
        reply = "Revision attempted but no changes were committed. React with 👍 to merge as-is, or leave another comment."

    new_bot_comment_id: int | None = None
    try:
        bot_reply = gh.post_comment(owner, repo, pr_number, reply)
        new_bot_comment_id = bot_reply["id"]
    except Exception as exc:
        logger.warning(json.dumps({"event": "pr_reply_failed", "task_id": task_id, "error": str(exc)}))

    # Persist updated state
    updated_bot_ids = list(bot_comment_ids)
    if new_bot_comment_id:
        updated_bot_ids.append(new_bot_comment_id)
    updated_processed = list(processed_human_ids | {latest_id})

    state["loop_count"] = loop_count + 1
    state["last_bot_comment_id"] = new_bot_comment_id
    state["bot_comment_ids"] = updated_bot_ids
    state["processed_human_comment_ids"] = updated_processed
    state_file.write_text(json.dumps(state, indent=2))

    return 1


def backfill_pr_reviews(
    plane_client: PlaneClient,
    service: ExecutionService,
    logger: logging.Logger,
) -> int:
    """Scan GitHub for open PRs on configured repos and create missing state files. Returns count created."""
    PR_REVIEW_STATE_DIR.mkdir(parents=True, exist_ok=True)
    created = 0

    for repo_key, repo_cfg in service.settings.repos.items():
        if not repo_cfg.await_review:
            continue
        token = service.settings.repo_git_token(repo_key)
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
            logger.warning(json.dumps({"event": "backfill_list_prs_failed", "repo_key": repo_key, "error": str(exc)}))
            continue

        for pr in open_prs:
            branch = pr.get("head", {}).get("ref", "")
            m = _BRANCH_TASK_ID_RE.match(branch)
            if not m:
                continue

            task_id = m.group(1)
            state_file = PR_REVIEW_STATE_DIR / f"{task_id}.json"
            if state_file.exists():
                continue

            # Fetch task from Plane to get goal and base branch
            try:
                issue = plane_client.fetch_issue(task_id)
                task = plane_client.to_board_task(issue)
                original_goal = task.goal_text
                base = task.base_branch or repo_cfg.default_branch
            except Exception:
                original_goal = ""
                base = pr.get("base", {}).get("ref", repo_cfg.default_branch)

            state = {
                "owner": owner,
                "repo": repo_name,
                "repo_key": repo_key,
                "pr_number": pr["number"],
                "pr_url": pr["html_url"],
                "task_id": task_id,
                "branch": branch,
                "base": base,
                "original_goal": original_goal,
                "created_at": pr.get("created_at") or datetime.now(UTC).isoformat(),
                "loop_count": 0,
                "last_bot_comment_id": None,
                "bot_comment_ids": [],
                "processed_human_comment_ids": [],
            }
            state_file.write_text(json.dumps(state, indent=2))
            created += 1
            logger.info(json.dumps({"event": "backfill_state_created", "task_id": task_id, "pr_number": pr["number"], "repo_key": repo_key}))

    logger.info(json.dumps({"event": "backfill_complete", "created": created}))
    return created


def run_review_loop(
    plane_client: PlaneClient,
    service: ExecutionService,
    *,
    poll_interval_seconds: int,
    max_cycles: int | None,
    status_dir: Path | None,
) -> None:
    logger = logging.getLogger(__name__)
    cycle = 0

    # Backfill any existing open PRs that predate this watcher
    backfill_pr_reviews(plane_client, service, logger)

    while True:
        cycle += 1
        cycle_run_id = f"review-cycle-{cycle}"
        logger.info(json.dumps({"event": "review_cycle_start", "cycle": cycle, "run_id": cycle_run_id}))

        try:
            states = _load_pr_states()
            actions = 0
            for state_file, state in states:
                try:
                    actions += _process_pr_state(state_file, state, plane_client, service, logger)
                except Exception as exc:
                    logger.warning(json.dumps({
                        "event": "pr_state_error",
                        "task_id": state.get("task_id"),
                        "error": str(exc),
                        "run_id": cycle_run_id,
                    }))

            logger.info(json.dumps({
                "event": "review_cycle_end",
                "cycle": cycle,
                "open_prs": len(states),
                "actions": actions,
                "run_id": cycle_run_id,
            }))

            if status_dir:
                status_dir.mkdir(parents=True, exist_ok=True)
                (status_dir / "review.status.json").write_text(json.dumps({
                    "cycle": cycle,
                    "open_prs": len(states),
                    "actions": actions,
                    "updated_at": datetime.now(UTC).isoformat(),
                }, indent=2))

        except Exception as exc:
            logger.warning(json.dumps({"event": "review_cycle_error", "cycle": cycle, "error": str(exc)}))

        if max_cycles is not None and cycle >= max_cycles:
            logger.info(json.dumps({"event": "review_loop_complete", "cycles": cycle}))
            return

        time.sleep(poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll open PRs for review reactions and run revision loops")
    parser.add_argument("--config", required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--backfill", action="store_true", help="Backfill state files for existing open PRs then exit")
    parser.add_argument("--poll-interval-seconds", type=int, default=60)
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--status-dir")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    os.environ.setdefault("CONTROL_PLANE_CONFIG", args.config)

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    service = ExecutionService(settings)

    logger = logging.getLogger(__name__)

    if args.backfill:
        backfill_pr_reviews(client, service, logger)
    elif args.watch:
        run_review_loop(
            client,
            service,
            poll_interval_seconds=args.poll_interval_seconds,
            max_cycles=args.max_cycles,
            status_dir=Path(args.status_dir) if args.status_dir else None,
        )
    else:
        states = _load_pr_states()
        for state_file, state in states:
            _process_pr_state(state_file, state, client, service, logger)


if __name__ == "__main__":
    main()
