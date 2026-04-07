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
_MERGE_CONFLICT_RE = re.compile(r"merge conflict|unresolved .* conflict", re.IGNORECASE)

PR_REVIEW_STATE_DIR = Path("state/pr_reviews")
PROPOSAL_FEEDBACK_DIR = Path("state/proposal_feedback")
REVIEW_TIMEOUT_SECONDS = 86400  # 1 day
MAX_CI_FIX_ATTEMPTS = 2


def _write_proposal_feedback(state: dict, outcome: str, merge_reason: str | None) -> None:
    """Write a feedback record so ProposalOutcomeDeriver can track proposal acceptance rates."""
    try:
        PROPOSAL_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        task_id = state["task_id"]
        record = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "task_id": task_id,
            "pr_number": state.get("pr_number"),
            "outcome": outcome,
            "merge_reason": merge_reason,
            "ci_fix_needed": state.get("ci_fix_attempts", 0) > 0,
            "self_review_loops": state.get("self_review_loops", 0),
            "human_review_loops": state.get("loop_count", 0),
        }
        (PROPOSAL_FEEDBACK_DIR / f"{task_id}.json").write_text(json.dumps(record, indent=2))
    except Exception:
        pass  # feedback is best-effort; never block the merge path


_REJECTION_PATTERNS_PATH = Path("state/rejection_patterns.json")

# Common rejection reason keywords → normalized pattern labels
_REJECTION_PATTERN_MAP = [
    (re.compile(r"missing test|no test|needs test|add test", re.IGNORECASE), "missing_tests"),
    (re.compile(r"naming convention|variable name|rename|name should", re.IGNORECASE), "naming_convention"),
    (re.compile(r"missing docstring|needs docstring|add docstring|no docstring", re.IGNORECASE), "missing_docstrings"),
    (re.compile(r"coverage|uncovered|untested branch", re.IGNORECASE), "coverage_gap"),
    (re.compile(r"style|formatting|format|whitespace|blank line", re.IGNORECASE), "code_style"),
    (re.compile(r"too large|too big|scope|too many files|split", re.IGNORECASE), "scope_too_large"),
    (re.compile(r"type annotation|type hint|missing type|typed", re.IGNORECASE), "missing_type_annotations"),
    (re.compile(r"breaking change|backwards compat|api change", re.IGNORECASE), "breaking_change"),
]


def _extract_rejection_patterns(comments: list[dict], *, family: str = "", repo_key: str = "") -> list[str]:
    """Scan PR review comments for known rejection patterns.  Returns pattern labels found."""
    found: set[str] = set()
    for comment in comments:
        body = str(comment.get("body") or "")
        for pattern_re, label in _REJECTION_PATTERN_MAP:
            if pattern_re.search(body):
                found.add(label)
    return sorted(found)


def _record_rejection_patterns(patterns: list[str], *, family: str, repo_key: str) -> None:
    """Append detected rejection patterns to the persistent rejection patterns store."""
    if not patterns:
        return
    try:
        _REJECTION_PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(_REJECTION_PATTERNS_PATH.read_text())
        except Exception:
            existing = {}
        key = f"{repo_key}:{family}" if (repo_key and family) else (family or repo_key or "unknown")
        entry = existing.setdefault(key, {"patterns": {}, "last_seen": {}})
        now_str = datetime.now(UTC).isoformat()
        for p in patterns:
            entry["patterns"][p] = entry["patterns"].get(p, 0) + 1
            entry["last_seen"][p] = now_str
        _REJECTION_PATTERNS_PATH.write_text(json.dumps(existing, indent=2))
    except Exception:
        pass  # best-effort


def load_rejection_patterns(*, family: str, repo_key: str) -> list[str]:
    """Return the most common rejection patterns for (repo_key, family), sorted by frequency."""
    try:
        data = json.loads(_REJECTION_PATTERNS_PATH.read_text())
        key = f"{repo_key}:{family}" if (repo_key and family) else (family or repo_key or "unknown")
        entry = data.get(key, {})
        by_count = sorted(entry.get("patterns", {}).items(), key=lambda kv: kv[1], reverse=True)
        return [p for p, _ in by_count[:3]]
    except Exception:
        return []


def _check_pr_description_quality(
    gh: GitHubPRClient,
    owner: str,
    repo: str,
    pr_number: int,
    *,
    task_description: str,
    marker: str,
    logger: logging.Logger,
) -> None:
    """Post a description enhancement comment when the PR description is too thin.

    This runs before self-review so reviewers see a useful description.
    A thin description is one that is empty, very short (< 80 chars), or consists
    only of the branch name / task ID.
    """
    try:
        pr_data = gh.get_pr(owner, repo, pr_number)
        body = str(pr_data.get("body") or "").strip()
        if len(body) >= 80:
            return  # Description is adequate

        # Build an enhanced description from the Plane task description if available
        task_excerpt = (task_description or "").strip()[:300]
        if not task_excerpt:
            return  # Nothing to add

        enhanced = (
            "**Description (auto-generated from task context):**\n\n"
            f"{task_excerpt}\n\n"
            "_This description was added automatically because the PR description was empty or too short._"
        )
        gh.update_pr_description(owner, repo, pr_number, enhanced)
        logger.info(json.dumps({
            "event": "pr_description_enhanced",
            "pr_number": pr_number,
            "original_length": len(body),
        }))
    except Exception:
        pass  # best-effort; never block self-review


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


def _concerns_indicate_merge_conflict(concerns: list[str]) -> bool:
    return any(_MERGE_CONFLICT_RE.search(c) for c in concerns)


def _try_auto_rebase(state: dict, service: ExecutionService, logger: logging.Logger) -> bool:
    """Clone the branch, rebase onto origin/base, force-push. Returns True on success."""
    repo_key = state["repo_key"]
    branch = state["branch"]
    base = state["base"]
    task_id = state["task_id"]
    try:
        repo_cfg = service.settings.repos[repo_key]
        return service.rebase_branch(
            clone_url=repo_cfg.clone_url,
            branch=branch,
            base_branch=base,
            task_id=task_id,
        )
    except Exception as exc:
        logger.warning(json.dumps({"event": "auto_rebase_exception", "task_id": task_id, "error": str(exc)}))
        return False


def _load_pr_states() -> list[tuple[Path, dict]]:
    if not PR_REVIEW_STATE_DIR.exists():
        return []
    logger = logging.getLogger(__name__)
    states = []
    for f in sorted(PR_REVIEW_STATE_DIR.glob("*.json")):
        try:
            states.append((f, json.loads(f.read_text())))
        except Exception as exc:
            logger.warning("Failed to load state file %s: %s", f, exc)
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

    # Check current PR state before attempting merge — it may already be merged or closed.
    try:
        pr_data = gh.get_pr(owner, repo, pr_number)
        pr_state = pr_data.get("state", "")
        merged = pr_data.get("merged", False)
        if merged or pr_state == "closed":
            logger.info(json.dumps({
                "event": "pr_already_closed",
                "task_id": task_id,
                "pr_number": pr_number,
                "pr_state": pr_state,
                "merged": merged,
            }))
            # PR is gone — clean up state and mark task Done anyway
            try:
                plane_client.transition_issue(task_id, "Done")
                plane_client.comment_issue(task_id, f"[Review] PR already {'merged' if merged else 'closed'} — marking Done.\n- pr_url: {pr_url}")
            except Exception as exc:
                logger.warning(json.dumps({"event": "plane_update_failed", "task_id": task_id, "error": str(exc)}))
            state_file.unlink(missing_ok=True)
            return
        # If CI checks are still running or failing, skip merge this cycle and retry later.
        mergeable_state = pr_data.get("mergeable_state", "")
        if mergeable_state == "unstable":
            logger.info(json.dumps({
                "event": "pr_merge_skipped_ci",
                "task_id": task_id,
                "pr_number": pr_number,
                "mergeable_state": mergeable_state,
                "reason": "CI checks failing or pending — will retry next cycle",
            }))
            return
    except Exception as exc:
        logger.warning(json.dumps({"event": "pr_state_check_failed", "task_id": task_id, "error": str(exc)}))
        # Continue and attempt merge anyway; merge_pr will fail if PR is gone

    try:
        gh.merge_pr(owner, repo, pr_number)
        logger.info(json.dumps({"event": "pr_merged", "task_id": task_id, "pr_number": pr_number, "reason": reason}))
    except Exception as exc:
        logger.warning(json.dumps({"event": "pr_merge_failed", "task_id": task_id, "error": str(exc)}))
        return

    _write_proposal_feedback(state, outcome="merged", merge_reason=reason)

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

    # S9-8: Ensure PR description is adequate before self-review runs.
    if not state.get("description_checked"):
        try:
            _task_desc = ""
            try:
                _issue = plane_client.fetch_issue(task_id)
                _task_desc = str(_issue.get("description") or _issue.get("description_stripped") or "")
            except Exception:
                pass
            _check_pr_description_quality(
                gh, owner, repo, pr_number,
                task_description=_task_desc,
                marker=marker,
                logger=logger,
            )
        except Exception:
            pass
        state["description_checked"] = True
        state_file.write_text(json.dumps(state, indent=2))

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
        # Option B: before merging, check whether CI is passing.
        try:
            pr_data = gh.get_pr(owner, repo, pr_number)
            if pr_data.get("mergeable_state") == "unstable":
                # CI is failing — hand off to the CI-fix phase instead of merging.
                state["phase"] = "awaiting_ci"
                state_file.write_text(json.dumps(state, indent=2))
                logger.info(json.dumps({
                    "event": "self_review_lgtm_awaiting_ci",
                    "task_id": task_id,
                    "pr_number": pr_number,
                }))
                return 1
        except Exception as exc:
            logger.warning(json.dumps({"event": "pr_ci_check_failed", "task_id": task_id, "error": str(exc)}))
        try:
            _post_bot_comment(gh, owner, repo, pr_number,
                              "Self-review passed — merging.", marker)
        except Exception as exc:
            logger.warning("Failed to post self-review merge comment for PR %s: %s", pr_number, exc)
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="self_review_lgtm")
        return 1

    # CONCERNS — check for merge conflicts first (Option A).
    if _concerns_indicate_merge_conflict(verdict.concerns) and not state.get("auto_rebase_attempted"):
        logger.info(json.dumps({"event": "auto_rebase_start", "task_id": task_id}))
        rebased = _try_auto_rebase(state, service, logger)
        state["auto_rebase_attempted"] = True
        state_file.write_text(json.dumps(state, indent=2))
        if rebased:
            # Rebase succeeded — let the next cycle retry self-review fresh.
            logger.info(json.dumps({"event": "auto_rebase_succeeded_retrying", "task_id": task_id}))
            return 1
        logger.info(json.dumps({"event": "auto_rebase_failed_falling_through", "task_id": task_id}))
        # Fall through to normal revision/escalation below.

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

    # Run a revision pass to address the concerns.
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

    # If the revision pass made no changes, further loops will not help — escalate now.
    if not changed_files:
        concerns_text = "\n".join(f"- {c}" for c in verdict.concerns)
        escalation_msg = (
            f"Self-review flagged concerns but the revision pass produced no changes:\n"
            f"{concerns_text}\n\nPlease review and comment, or react with 👍 to merge as-is."
        )
        logger.info(json.dumps({"event": "self_review_no_progress_escalate", "task_id": task_id}))
        _escalate_to_human(gh, state, state_file, plane_client, logger, service.settings,
                           reason=escalation_msg)
        return 1

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
    _write_proposal_feedback(state, outcome="escalated", merge_reason=None)

    # S9-5: Extract rejection patterns from current review comments
    try:
        _family = state.get("source_family", "")
        _repo_key = state.get("repo_key", "")
        _comments = gh.list_pr_comments(owner, repo, pr_number)
        _patterns = _extract_rejection_patterns(_comments, family=_family, repo_key=_repo_key)
        if _patterns:
            _record_rejection_patterns(_patterns, family=_family, repo_key=_repo_key)
            logger.info(json.dumps({
                "event": "rejection_patterns_recorded",
                "task_id": task_id,
                "patterns": _patterns,
            }))
    except Exception:
        pass


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

    # S6-3: Auto-merge for autonomy tasks when CI is green and config permits.
    repo_cfg = service.settings.repos.get(repo_key)
    if repo_cfg and getattr(repo_cfg, "auto_merge_on_ci_green", False):
        try:
            _issue = plane_client.fetch_issue(task_id)
            _labels = [
                str(lbl.get("name", lbl) if isinstance(lbl, dict) else lbl)
                for lbl in (_issue.get("labels") or [])
            ]
            _is_autonomy = any("source: autonomy" in lbl or lbl == "source: autonomy" for lbl in _labels)
            if _is_autonomy:
                _pr_data = gh.get_pr(owner, repo, pr_number)
                _failed = gh.get_failed_checks(owner, repo, pr_number, pr_data=_pr_data)
                _threshold = float(getattr(reviewer_cfg, "auto_merge_success_rate_threshold", 0.9))
                # Only auto-merge when recent success rate is healthy (or we have no data yet).
                _rate = service.usage_store.check_failure_rate_degradation(now=datetime.now(UTC))
                _rate_ok = _rate is None or _rate >= _threshold
                if not _failed and _rate_ok and not _pr_data.get("merged") and _pr_data.get("state") == "open":
                    logger.info(json.dumps({
                        "event": "pr_auto_merge_ci_green",
                        "task_id": task_id,
                        "reason": "autonomy_task_ci_green",
                    }))
                    _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="auto_merge_ci_green")
                    return 1
        except Exception as _exc:
            logger.warning(json.dumps({"event": "pr_auto_merge_check_failed", "task_id": task_id, "error": str(_exc)}))

    # S8-6: Proactive branch divergence detection — if the branch is behind base,
    # attempt a rebase before it becomes a conflict, not just when self-review flags it.
    try:
        _pr_for_divergence = gh.get_pr(owner, repo, pr_number)
        _ms = _pr_for_divergence.get("mergeable_state", "")
        if _ms == "behind" and not state.get("auto_rebase_attempted"):
            logger.info(json.dumps({
                "event": "branch_divergence_detected",
                "task_id": task_id,
                "pr_number": pr_number,
                "mergeable_state": _ms,
            }))
            _rebased = _try_auto_rebase(state, service, logger)
            state["auto_rebase_attempted"] = True
            state_file.write_text(json.dumps(state, indent=2))
            if _rebased:
                logger.info(json.dumps({"event": "divergence_rebase_succeeded", "task_id": task_id}))
                return 1
            else:
                logger.warning(json.dumps({"event": "divergence_rebase_failed", "task_id": task_id}))
    except Exception as _exc:
        logger.warning(json.dumps({"event": "divergence_check_error", "task_id": task_id, "error": str(_exc)}))

    # Timeout: merge after 1 day with no action
    created_at = datetime.fromisoformat(state["created_at"])
    elapsed = (datetime.now(UTC) - created_at).total_seconds()
    if elapsed > REVIEW_TIMEOUT_SECONDS:
        logger.info(json.dumps({"event": "pr_review_timeout", "task_id": task_id, "elapsed_hours": round(elapsed / 3600, 1)}))
        # S8-9: Respect require_explicit_approval — never timeout-merge if set.
        repo_cfg_for_approval = service.settings.repos.get(repo_key)
        if repo_cfg_for_approval and getattr(repo_cfg_for_approval, "require_explicit_approval", False):
            logger.info(json.dumps({
                "event": "pr_timeout_merge_skipped_explicit_approval",
                "task_id": task_id,
                "reason": "require_explicit_approval is true for this repo",
            }))
            # Post a reminder comment at most once per day to avoid comment spam
            _reminder_key = f"explicit_approval_reminder_{task_id}"
            _last_reminder = state.get(_reminder_key, "")
            _now_str = datetime.now(UTC).isoformat()
            if not _last_reminder or (datetime.now(UTC) - datetime.fromisoformat(_last_reminder)).total_seconds() > 86400:
                try:
                    marker = _bot_marker(service.settings)
                    _post_bot_comment(
                        gh, owner, repo, pr_number,
                        "This PR requires explicit approval before merging. "
                        "React with 👍 or leave an approval comment to proceed.",
                        marker,
                    )
                    state[_reminder_key] = _now_str
                    state_file.write_text(json.dumps(state, indent=2))
                except Exception:
                    pass
            return 0
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
        except Exception as exc:
            logger.warning("Failed to post max-loops notice for PR %s: %s", pr_number, exc)
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

    # Zero-change revision: don't burn a loop count — the comment was processed but
    # nothing was committed, so further loops are unlikely to help either.
    if not changed_files:
        reply_body = (
            "Revision attempted but kodo made no changes. This may mean the request "
            "is already addressed, or kodo needs more specific instructions. "
            "React with 👍 to merge as-is, or leave a more detailed comment."
        )
        logger.info(json.dumps({"event": "human_revision_no_changes", "task_id": task_id}))
        new_bot_comment_id: int | None = None
        try:
            bot_reply = _post_bot_comment(gh, owner, repo, pr_number, reply_body, marker)
            new_bot_comment_id = bot_reply["id"]
        except Exception as exc:
            logger.warning(json.dumps({"event": "pr_reply_failed", "task_id": task_id, "error": str(exc)}))
        updated_bot_ids = list(bot_comment_ids)
        if new_bot_comment_id:
            updated_bot_ids.append(new_bot_comment_id)
        # Mark comment as processed but do NOT increment loop_count
        state["last_bot_comment_id"] = new_bot_comment_id
        state["bot_comment_ids"] = updated_bot_ids
        state["processed_human_comment_ids"] = list(processed_human_ids | {latest_id})
        state_file.write_text(json.dumps(state, indent=2))
        return 1

    reply_body = "Revision applied. React with 👍 to merge, or leave another comment for further changes."

    new_bot_comment_id = None
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


def _process_awaiting_ci(
    state_file: Path,
    state: dict,
    plane_client: PlaneClient,
    service: ExecutionService,
    logger: logging.Logger,
) -> int:
    """CI-fix phase: self-review passed but CI is failing.

    Each cycle: if CI cleared → merge; if still failing → run a kodo fix pass.
    After MAX_CI_FIX_ATTEMPTS failures → escalate to human.
    """
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

    try:
        pr_data = gh.get_pr(owner, repo, pr_number)
    except Exception as exc:
        logger.warning(json.dumps({"event": "pr_state_check_failed", "task_id": task_id, "error": str(exc)}))
        return 0

    mergeable_state = pr_data.get("mergeable_state", "")
    if mergeable_state != "unstable":
        # CI cleared (or state is unknown/clean) — proceed to merge.
        logger.info(json.dumps({"event": "ci_cleared_merging", "task_id": task_id, "mergeable_state": mergeable_state}))
        try:
            _post_bot_comment(gh, owner, repo, pr_number, "CI checks passed — merging.", marker)
        except Exception as exc:
            logger.warning("Failed to post CI-cleared comment for PR %s: %s", pr_number, exc)
        _merge_and_finalize(gh, state, state_file, plane_client, logger, reason="self_review_lgtm")
        return 1

    # CI still failing.
    ci_fix_attempts = state.get("ci_fix_attempts", 0)
    if ci_fix_attempts >= MAX_CI_FIX_ATTEMPTS:
        failed = gh.get_failed_checks(owner, repo, pr_number, pr_data=pr_data)
        failures_text = "\n".join(f"- {f}" for f in (failed or ["(unknown)"]))
        reason = (
            f"CI is still failing after {ci_fix_attempts} automated fix attempt(s). "
            f"Failing checks:\n{failures_text}\n\n"
            f"Please review and fix manually, or react with 👍 to merge as-is."
        )
        logger.info(json.dumps({"event": "ci_fix_exhausted_escalating", "task_id": task_id, "attempts": ci_fix_attempts}))
        _escalate_to_human(gh, state, state_file, plane_client, logger, service.settings, reason=reason)
        return 1

    failed = gh.get_failed_checks(owner, repo, pr_number, pr_data=pr_data)
    if not failed:
        # Can't identify what's failing yet — wait for checks to finish.
        logger.info(json.dumps({"event": "ci_fix_waiting_for_checks", "task_id": task_id}))
        return 0

    repo_cfg = service.settings.repos[repo_key]
    ci_fix_comment = (
        "CI checks are failing. Fix the following failures without changing the intent of the "
        "original changes:\n" + "\n".join(f"- {f}" for f in failed)
    )
    logger.info(json.dumps({
        "event": "ci_fix_start",
        "task_id": task_id,
        "attempt": ci_fix_attempts + 1,
        "failures": failed,
    }))
    _, changed_files = service.run_review_pass(
        repo_key=repo_key,
        clone_url=repo_cfg.clone_url,
        branch=state["branch"],
        base_branch=state["base"],
        original_goal=state.get("original_goal", ""),
        review_comment=ci_fix_comment,
        task_id=task_id,
    )
    state["ci_fix_attempts"] = ci_fix_attempts + 1
    logger.info(json.dumps({
        "event": "ci_fix_end",
        "task_id": task_id,
        "changed_files": len(changed_files),
    }))
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
    elif phase == "awaiting_ci":
        return _process_awaiting_ci(state_file, state, plane_client, service, logger)
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
