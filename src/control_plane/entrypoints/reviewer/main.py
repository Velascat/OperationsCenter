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


def _bot_marker(settings) -> str:
    return settings.reviewer.bot_comment_marker


def _post_bot_comment(gh: GitHubPRClient, owner: str, repo: str, pr_number: int, body: str, marker: str) -> dict:
    """Post a comment tagged with the bot marker so future cycles can filter it out."""
    return gh.post_comment(owner, repo, pr_number, f"{body}\n\n{marker}")


def _is_bot_comment(comment: dict, bot_comment_ids: set[int], bot_logins: set[str], marker: str) -> bool:
    """Return True if this comment should be ignored as a bot comment."""
    if comment["id"] in bot_comment_ids:
        return True
    login = (comment.get("user") or {}).get("login", "")
    if login in bot_logins:
        return True
    if marker in (comment.get("body") or ""):
        return True
    return False


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


def _process_self_review(
    state_file: Path,
    state: dict,
    plane_client: PlaneClient,
    service: ExecutionService,
    logger: logging.Logger,
) -> int:
    """Self-review phase: kodo reviews its own diff and either merges or escalates."""
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
    marker = _bot_marker(service.settings)

    # Timeout: escalate to human rather than merge blindly from self-review
    created_at = datetime.fromisoformat(state["created_at"])
    elapsed = (datetime.now(UTC) - created_at).total_seconds()
    if elapsed > REVIEW_TIMEOUT_SECONDS:
        logger.info(json.dumps({"event": "pr_self_review_timeout", "task_id": task_id}))
        _escalate_to_human(gh, state, state_file, plane_client, logger, service.settings,
                           reason="Self-review timed out — please review manually.")
        return 1

    max_loops = service.settings.reviewer.max_self_review_loops
    self_review_loops = state.get("self_review_loops", 0)

    repo_cfg = service.settings.repos[repo_key]

    logger.info(json.dumps({
        "event": "self_review_start",
        "task_id": task_id,
        "loop": self_review_loops,
    }))

    verdict = service.run_self_review_pass(
        repo_key=repo_key,
        clone_url=repo_cfg.clone_url,
        branch=state["branch"],
        base_branch=state["base"],
        original_goal=state.get("original_goal", ""),
        task_id=task_id,
    )

    logger.info(json.dumps({
        "event": "self_review_verdict",
        "task_id": task_id,
        "verdict": verdict.verdict,
        "concerns": verdict.concerns,
        "loop": self_review_loops,
    }))

    if verdict.verdict == "lgtm":
        try:
            _post_bot_comment(gh, owner, repo, pr_number,
                              "Self-review passed — merging.", marker)
        except Exception:
            pass
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="self_review_lgtm")
        return 1

    # CONCERNS or error
    if self_review_loops >= max_loops:
        concerns_text = "\n".join(f"- {c}" for c in verdict.concerns)
        escalation_msg = (
            f"Self-review flagged concerns after {self_review_loops} revision attempt(s) "
            f"and could not resolve them:\n{concerns_text}\n\nPlease review and comment, "
            f"or react with 👍 to merge as-is."
        )
        _escalate_to_human(gh, state, state_file, plane_client, logger, service.settings,
                           reason=escalation_msg)
        return 1

    # Run a revision pass to address the concerns
    concerns_comment = "Address the following self-review concerns:\n" + "\n".join(
        f"- {c}" for c in verdict.concerns
    )
    logger.info(json.dumps({
        "event": "self_review_revision_start",
        "task_id": task_id,
        "loop": self_review_loops,
    }))

    success, changed_files = service.run_review_pass(
        repo_key=repo_key,
        clone_url=repo_cfg.clone_url,
        branch=state["branch"],
        base_branch=state["base"],
        original_goal=state.get("original_goal", ""),
        review_comment=concerns_comment,
        task_id=task_id,
    )

    logger.info(json.dumps({
        "event": "self_review_revision_end",
        "task_id": task_id,
        "success": success,
        "changed_files": len(changed_files),
    }))

    state["self_review_loops"] = self_review_loops + 1
    state_file.write_text(json.dumps(state, indent=2))
    return 1


def _escalate_to_human(
    gh: GitHubPRClient,
    state: dict,
    state_file: Path,
    plane_client: PlaneClient,
    logger: logging.Logger,
    settings,
    *,
    reason: str,
) -> None:
    """Transition state to human_review phase and post a comment on the PR."""
    marker = settings.reviewer.bot_comment_marker
    owner = state["owner"]
    repo = state["repo"]
    pr_number = state["pr_number"]
    task_id = state["task_id"]

    try:
        reply = _post_bot_comment(gh, owner, repo, pr_number, reason, marker)
        bot_ids = list(state.get("bot_comment_ids", []))
        bot_ids.append(reply["id"])
        state["bot_comment_ids"] = bot_ids
        state["last_bot_comment_id"] = reply["id"]
    except Exception as exc:
        logger.warning(json.dumps({"event": "escalation_comment_failed", "task_id": task_id, "error": str(exc)}))

    state["phase"] = "human_review"
    state_file.write_text(json.dumps(state, indent=2))
    logger.info(json.dumps({"event": "escalated_to_human_review", "task_id": task_id}))


def _process_human_review(
    state_file: Path,
    state: dict,
    plane_client: PlaneClient,
    service: ExecutionService,
    logger: logging.Logger,
) -> int:
    """Human review phase: respond to reviewer comments, merge on 👍."""
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
    marker = _bot_marker(service.settings)

    reviewer_cfg = service.settings.reviewer
    bot_logins: set[str] = set(reviewer_cfg.bot_logins)
    allowed_logins: set[str] = set(reviewer_cfg.allowed_reviewer_logins)

    # Timeout: merge after 1 day with no action
    created_at = datetime.fromisoformat(state["created_at"])
    elapsed = (datetime.now(UTC) - created_at).total_seconds()
    if elapsed > REVIEW_TIMEOUT_SECONDS:
        logger.info(json.dumps({"event": "pr_review_timeout", "task_id": task_id, "elapsed_hours": round(elapsed / 3600, 1)}))
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="timeout")
        return 1

    # 👍 on the PR → merge
    pr_reactions = gh.get_pr_reactions(owner, repo, pr_number)
    if gh.has_thumbs_up(pr_reactions):
        logger.info(json.dumps({"event": "pr_approved_thumbs_up", "task_id": task_id}))
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="approved")
        return 1

    # 👍 on the last bot comment → merge
    last_bot_comment_id = state.get("last_bot_comment_id")
    if last_bot_comment_id:
        comment_reactions = gh.get_comment_reactions(owner, repo, last_bot_comment_id)
        if gh.has_thumbs_up(comment_reactions):
            logger.info(json.dumps({"event": "pr_comment_approved", "task_id": task_id}))
            _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="comment_approved")
            return 1

    # Collect new human comments (conversation + inline review)
    bot_comment_ids: set[int] = set(state.get("bot_comment_ids", []))
    processed_human_ids: set[int] = set(state.get("processed_human_comment_ids", []))

    all_comments = gh.list_pr_comments(owner, repo, pr_number)
    try:
        review_comments = gh.list_pr_review_comments(owner, repo, pr_number)
        for rc in review_comments:
            rc.setdefault("_source", "review")
        all_comments = all_comments + review_comments
    except Exception as exc:
        logger.warning(json.dumps({"event": "pr_review_comments_failed", "task_id": task_id, "error": str(exc)}))

    new_human_comments = [
        c for c in all_comments
        if not _is_bot_comment(c, bot_comment_ids, bot_logins, marker)
        and c["id"] not in processed_human_ids
        and (not allowed_logins or (c.get("user") or {}).get("login", "") in allowed_logins)
    ]

    if not new_human_comments:
        return 0

    latest_comment = new_human_comments[-1]
    latest_id = latest_comment["id"]
    loop_count = state.get("loop_count", 0)
    max_loops = 3

    if loop_count >= max_loops:
        logger.info(json.dumps({"event": "pr_max_loops_reached", "task_id": task_id, "loop_count": loop_count}))
        try:
            notice = _post_bot_comment(gh, owner, repo, pr_number,
                                       "Maximum revision loops (3) reached — merging as-is.", marker)
            bot_comment_ids.add(notice["id"])
        except Exception:
            pass
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="max_loops")
        return 1

    review_comment = latest_comment["body"]
    if latest_comment.get("_source") == "review" and latest_comment.get("path"):
        review_comment = f"[{latest_comment['path']}]\n{review_comment}"

    repo_cfg = service.settings.repos[repo_key]
    logger.info(json.dumps({
        "event": "human_revision_start",
        "task_id": task_id,
        "loop_count": loop_count,
        "comment_id": latest_id,
        "from_login": (latest_comment.get("user") or {}).get("login", ""),
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
        "event": "human_revision_end",
        "task_id": task_id,
        "success": success,
        "changed_files": len(changed_files),
    }))

    reply_body = (
        "Revision applied. React with 👍 to merge, or leave another comment for further changes."
        if success
        else "Revision attempted but no changes were committed. React with 👍 to merge as-is, or leave another comment."
    )

    new_bot_comment_id: int | None = None
    try:
        bot_reply = _post_bot_comment(gh, owner, repo, pr_number, reply_body, marker)
        new_bot_comment_id = bot_reply["id"]
    except Exception as exc:
        logger.warning(json.dumps({"event": "pr_reply_failed", "task_id": task_id, "error": str(exc)}))

    updated_bot_ids = list(bot_comment_ids)
    if new_bot_comment_id:
        updated_bot_ids.append(new_bot_comment_id)

    state["loop_count"] = loop_count + 1
    state["last_bot_comment_id"] = new_bot_comment_id
    state["bot_comment_ids"] = updated_bot_ids
    state["processed_human_comment_ids"] = list(processed_human_ids | {latest_id})
    state_file.write_text(json.dumps(state, indent=2))
    return 1


def _process_pr_state(
    state_file: Path,
    state: dict,
    plane_client: PlaneClient,
    service: ExecutionService,
    logger: logging.Logger,
) -> int:
    """Dispatch to the appropriate phase handler. Returns 1 if an action was taken."""
    phase = state.get("phase", "self_review")
    if phase == "self_review":
        return _process_self_review(state_file, state, plane_client, service, logger)
    else:
        return _process_human_review(state_file, state, plane_client, service, logger)


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

            try:
                issue = plane_client.fetch_issue(task_id)
                task = plane_client.to_board_task(issue)
                original_goal = task.goal_text
                base = task.base_branch or repo_cfg.default_branch
            except Exception:
                original_goal = ""
                base = pr.get("base", {}).get("ref", repo_cfg.default_branch)

            state = {
                "phase": "self_review",
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
                "self_review_loops": 0,
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
    parser = argparse.ArgumentParser(description="Poll open PRs for self-review and human review loops")
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
