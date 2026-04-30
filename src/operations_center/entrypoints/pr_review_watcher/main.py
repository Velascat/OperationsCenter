# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""PR Review Watcher — two-phase state machine for PRs created by the goal lane.

Phase 1 (self-review): kodo reviews the diff and emits LGTM or CONCERNS.
  - LGTM → merge PR, mark Plane task Done.
  - CONCERNS → post comment, run revision pass, retry up to max_self_review_loops.
  - Unresolved → escalate to Phase 2.

Phase 2 (human review): poll PR comments for human approval or feedback.
  - /lgtm comment or 👍 reaction → merge, Done.
  - Human comment → kodo revision pass, post reply; up to max_human_review_loops.
  - Timeout (human_review_timeout_seconds from phase-2 entry) → auto-merge.

State per PR persisted in state/pr_reviews/<repo_key>-<pr_number>.json.
The state file is the single source of truth; Plane is updated after state is written.

CLI matches the reviewer role contract used by operations-center.sh:
    --config              path to operations_center.local.yaml
    --watch               run as a daemon (loop forever)
    --poll-interval-seconds N
    --status-dir          directory for heartbeat_review.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_SUBDIR = Path("state") / "pr_reviews"


# ── State file helpers ────────────────────────────────────────────────────────

def _state_key(repo_key: str, pr_number: int) -> str:
    return f"{repo_key}-{pr_number}"


def _state_path(oc_root: Path, repo_key: str, pr_number: int) -> Path:
    return oc_root / _STATE_SUBDIR / f"{_state_key(repo_key, pr_number)}.json"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    state = dict(state)
    state["updated_at"] = datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _new_state(repo_key: str, pr_number: int) -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "state_key":            _state_key(repo_key, pr_number),
        "pr_number":            pr_number,
        "repo_key":             repo_key,
        "phase":                "self_review",
        "self_review_loops":    0,
        "human_review_loops":   0,
        "processed_comment_ids": [],
        "plane_task_id":        None,
        "phase2_entered_at":    None,
        "created_at":           now,
        "updated_at":           now,
    }


# ── Settings / adapter helpers ────────────────────────────────────────────────

def _load_settings(config_path: Path):
    from operations_center.config import load_settings
    return load_settings(config_path)


def _github_client(settings):
    from operations_center.adapters.github_pr import GitHubPRClient
    token = settings.git_token()
    if not token:
        raise RuntimeError("no GitHub token — set GIT_TOKEN in .env")
    return GitHubPRClient(token)


def _plane_client(settings):
    from operations_center.adapters.plane import PlaneClient
    return PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )


def _owner_repo(clone_url: str) -> tuple[str, str]:
    from operations_center.adapters.github_pr import GitHubPRClient
    return GitHubPRClient.owner_repo_from_clone_url(clone_url)


def _venv_python(oc_root: Path) -> str:
    p = oc_root / ".venv" / "bin" / "python"
    return str(p) if p.exists() else "python3"


def _build_env(oc_root: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(oc_root / "src")
    return env


def _label_value(labels: list, prefix: str) -> str:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


# ── kodo pipeline ─────────────────────────────────────────────────────────────

def _run_pipeline(
    oc_root: Path,
    config_path: Path,
    repo_key: str,
    goal_text: str,
    settings,
    *,
    source: str,
    state_key: str,
    branch_suffix: str,
) -> dict | None:
    """Run worker.main → execute.main and return verdict.json contents, or None."""
    python  = _venv_python(oc_root)
    env     = _build_env(oc_root)
    repo_cfg = settings.repos.get(repo_key)
    if not repo_cfg:
        logger.error("pr_review_watcher: unknown repo_key=%s", repo_key)
        return None

    with tempfile.TemporaryDirectory(prefix="oc-review-") as tmpdir:
        tmp = Path(tmpdir)

        plan_cmd = [
            python, "-m", "operations_center.entrypoints.worker.main",
            "--goal",           goal_text,
            "--task-type",      "chore",
            "--execution-mode", "goal",
            "--repo-key",       repo_key,
            "--clone-url",      repo_cfg.clone_url,
            "--base-branch",    repo_cfg.default_branch,
            "--project-id",     settings.plane.project_id,
            "--task-id",        state_key,
        ]
        plan_proc = subprocess.run(plan_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        try:
            bundle = json.loads(plan_proc.stdout)
        except Exception:
            logger.error(
                "pr_review_watcher: planning produced no JSON for state_key=%s\n%s",
                state_key, (plan_proc.stderr or plan_proc.stdout).strip(),
            )
            return None

        if plan_proc.returncode != 0:
            logger.error(
                "pr_review_watcher: planning failed state_key=%s — %s",
                state_key, bundle.get("message", "unknown"),
            )
            return None

        bundle_file  = tmp / "bundle.json"
        config_copy  = tmp / "ops.yaml"
        workspace    = tmp / "workspace"
        result_file  = tmp / "result.json"

        bundle_file.write_text(json.dumps(bundle), encoding="utf-8")
        shutil.copy(config_path, config_copy)
        workspace.mkdir()

        exec_cmd = [
            python, "-m", "operations_center.entrypoints.execute.main",
            "--config",         str(config_copy),
            "--bundle",         str(bundle_file),
            "--workspace-path", str(workspace),
            "--task-branch",    f"review/{branch_suffix}",
            "--output",         str(result_file),
            "--source",         source,
        ]
        subprocess.run(exec_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        verdict_path = workspace / "verdict.json"
        if verdict_path.exists():
            try:
                return json.loads(verdict_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("pr_review_watcher: malformed verdict.json for state_key=%s", state_key)
        return None


# ── GitHub helpers ─────────────────────────────────────────────────────────────

def _is_bot_comment(comment: dict, bot_logins: set[str], bot_marker: str) -> bool:
    login = ((comment.get("user") or {}).get("login") or "").lower()
    if login in bot_logins:
        return True
    return bot_marker in (comment.get("body") or "")


def _is_lgtm_comment(comment: dict) -> bool:
    return (comment.get("body") or "").strip().lower() == "/lgtm"


# ── Merge + Plane done ────────────────────────────────────────────────────────

def _merge_and_done(
    state: dict,
    state_path: Path,
    pr_data: dict,
    gh_client,
    owner: str,
    repo: str,
    settings,
    *,
    reason: str,
) -> None:
    pr_number = state["pr_number"]
    try:
        gh_client.merge_pr(owner, repo, pr_number, merge_method="squash")
        logger.info("pr_review_watcher: merged PR #%d repo=%s reason=%s", pr_number, state["repo_key"], reason)
    except Exception as exc:
        logger.error("pr_review_watcher: merge failed PR #%d — %s", pr_number, exc)
        return  # leave state file — operator must inspect

    plane_task_id = state.get("plane_task_id")
    if plane_task_id:
        try:
            client = _plane_client(settings)
            try:
                client.transition_issue(plane_task_id, "Done")
                client.comment_issue(plane_task_id, f"PR #{pr_number} merged ({reason})")
            finally:
                client.close()
        except Exception as exc:
            logger.warning("pr_review_watcher: Plane Done failed task_id=%s — %s", plane_task_id, exc)

    state_path.unlink(missing_ok=True)


# ── Phase 1: self-review ──────────────────────────────────────────────────────

def _phase1(
    state: dict,
    state_path: Path,
    pr_data: dict,
    gh_client,
    owner: str,
    repo: str,
    oc_root: Path,
    config_path: Path,
    settings,
) -> None:
    pr_number   = int(state["pr_number"])
    repo_key    = state["repo_key"]
    state_key   = state["state_key"]
    reviewer    = settings.reviewer

    diff = gh_client.get_pr_diff(owner, repo, pr_number)
    if not diff:
        logger.warning("pr_review_watcher: empty diff PR #%d, skipping", pr_number)
        return

    diff_excerpt = diff[:8000] + ("\n...[diff truncated]" if len(diff) > 8000 else "")
    title        = pr_data.get("title", "")

    goal_text = (
        f"Review the following pull-request diff for correctness, style, and potential bugs.\n\n"
        f"PR #{pr_number}: {title}\n\n"
        f"```diff\n{diff_excerpt}\n```\n\n"
        f"Write your verdict as JSON to a file named `verdict.json` in the current working directory:\n"
        f'{{"result": "LGTM", "summary": "..."}}\n'
        f"or\n"
        f'{{"result": "CONCERNS", "summary": "bullet list of specific issues"}}\n\n'
        f"Use LGTM if the code is correct and ready to merge. "
        f"Use CONCERNS only if there are concrete, actionable issues. "
        f"Do NOT push any code changes to the repository."
    )

    logger.info(
        "pr_review_watcher: self-review PR #%d repo=%s loop=%d",
        pr_number, repo_key, state["self_review_loops"],
    )

    verdict = _run_pipeline(
        oc_root, config_path, repo_key, goal_text, settings,
        source="reviewer_self",
        state_key=state_key,
        branch_suffix=f"{state_key[:12]}",
    )

    state["self_review_loops"] += 1

    if verdict is None:
        logger.warning("pr_review_watcher: no verdict PR #%d — will retry next poll", pr_number)
        _save_state(state_path, state)
        return

    result  = (verdict.get("result") or "CONCERNS").upper()
    summary = verdict.get("summary", "(no summary)")

    logger.info("pr_review_watcher: PR #%d self-review verdict=%s", pr_number, result)

    if result == "LGTM":
        _merge_and_done(state, state_path, pr_data, gh_client, owner, repo, settings, reason="self_review_lgtm")
        return

    # CONCERNS — post comment and possibly escalate
    concern_body = (
        f"{reviewer.bot_comment_marker}\n"
        f"**Self-review (pass {state['self_review_loops']}) — concerns:**\n\n{summary}"
    )
    try:
        gh_client.post_comment(owner, repo, pr_number, concern_body)
    except Exception as exc:
        logger.warning("pr_review_watcher: failed to post concern comment PR #%d — %s", pr_number, exc)

    if state["self_review_loops"] >= reviewer.max_self_review_loops:
        state["phase"]             = "human_review"
        state["phase2_entered_at"] = datetime.now(UTC).isoformat()
        escalation_body = (
            f"{reviewer.bot_comment_marker}\n"
            f"**Escalated to human review** after {state['self_review_loops']} self-review pass(es).\n\n"
            f"Remaining concerns:\n\n{summary}\n\n"
            f"Reply `/lgtm` or add a 👍 reaction to approve and merge. "
            f"Leave a comment to request specific changes."
        )
        try:
            gh_client.post_comment(owner, repo, pr_number, escalation_body)
        except Exception as exc:
            logger.warning("pr_review_watcher: failed to post escalation comment PR #%d — %s", pr_number, exc)
        # Tag the corresponding Plane task with `lifecycle: escalated` so the
        # status pane and downstream services can recognise it as a task
        # that's exited normal automated flow (auto-promote skips, status
        # pane can highlight, etc.). Best-effort — the GitHub side is the
        # source of truth, this is just convenience metadata.
        plane_task_id = state.get("task_id")
        if plane_task_id:
            try:
                from operations_center.adapters.plane import PlaneClient
                pc = PlaneClient(
                    base_url=settings.plane.base_url,
                    api_token=settings.plane_token(),
                    workspace_slug=settings.plane.workspace_slug,
                    project_id=settings.plane.project_id,
                )
                try:
                    issue = pc.fetch_issue(plane_task_id)
                    existing = [
                        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
                        for lab in issue.get("labels", [])
                    ]
                    existing = [n for n in existing if n]
                    if "lifecycle: escalated" not in existing:
                        pc.update_issue_labels(plane_task_id, existing + ["lifecycle: escalated"])
                finally:
                    pc.close()
            except Exception as exc:
                logger.warning("pr_review_watcher: failed to label Plane task escalated — %s", exc)
        logger.info("pr_review_watcher: PR #%d escalated to human review", pr_number)

    _save_state(state_path, state)


# ── Phase 2: human review ─────────────────────────────────────────────────────

def _phase2(
    state: dict,
    state_path: Path,
    pr_data: dict,
    gh_client,
    owner: str,
    repo: str,
    oc_root: Path,
    config_path: Path,
    settings,
) -> None:
    pr_number = int(state["pr_number"])
    repo_key  = state["repo_key"]
    state_key = state["state_key"]
    reviewer  = settings.reviewer
    bot_logins = {login.lower() for login in reviewer.bot_logins}
    allowed    = {login.lower() for login in reviewer.allowed_reviewer_logins}

    # ── Timeout check ────────────────────────────────────────────────────────
    entered_at = state.get("phase2_entered_at")
    if entered_at:
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(entered_at)).total_seconds()
        if elapsed >= reviewer.human_review_timeout_seconds:
            hours = int(elapsed / 3600)
            notice = (
                f"{reviewer.bot_comment_marker}\n"
                f"**Auto-merging** — human review timed out after {hours}h."
            )
            try:
                gh_client.post_comment(owner, repo, pr_number, notice)
            except Exception:
                pass
            logger.info("pr_review_watcher: PR #%d human-review timeout after %.0fs", pr_number, elapsed)
            _merge_and_done(state, state_path, pr_data, gh_client, owner, repo, settings, reason="human_review_timeout")
            return

    # ── auto-merge-on-CI-green (C-K1) ────────────────────────────────────────
    # When the repo opts in via RepoSettings.auto_merge_on_ci_green AND the
    # task carries `source: autonomy`, the reviewer auto-merges the moment CI
    # passes — without waiting for the 24h timeout. Honors
    # RepoSettings.ci_ignored_checks (C-K7) so flaky / advisory checks don't
    # gate the merge.
    repo_cfg = settings.repos.get(repo_key)
    if repo_cfg and getattr(repo_cfg, "auto_merge_on_ci_green", False):
        # Only autonomy-sourced PRs participate — operator-launched runs
        # should still go through human review.
        labels_lower = [
            (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            for lab in (pr_data.get("labels", []) or [])
        ]
        # If labels aren't on the PR, fall back to the PR title prefix or
        # the linked Plane task source. PRs created by WorkspaceManager
        # don't carry labels today, so this is a soft check — we infer
        # autonomy from the branch prefix instead.
        head_ref = ((pr_data.get("head") or {}).get("ref") or "").lower()
        is_autonomy = (
            "source: autonomy" in labels_lower
            or head_ref.startswith(("goal/", "test/", "improve/"))
        )
        if is_autonomy:
            try:
                ignored = list(getattr(repo_cfg, "ci_ignored_checks", []) or [])
                failed = gh_client.get_failed_checks(
                    owner, repo, pr_number,
                    pr_data=pr_data,
                    ignored_checks=ignored,
                )
                if not failed:
                    logger.info(
                        "pr_review_watcher: PR #%d auto-merging — CI green and "
                        "auto_merge_on_ci_green=True", pr_number,
                    )
                    _merge_and_done(
                        state, state_path, pr_data, gh_client, owner, repo, settings,
                        reason="auto_merge_on_ci_green",
                    )
                    return
                logger.debug(
                    "pr_review_watcher: PR #%d not auto-merged — %d failed check(s) "
                    "(after ignoring %d)", pr_number, len(failed), len(ignored),
                )
            except Exception as exc:
                logger.debug("pr_review_watcher: CI status check failed PR #%d — %s", pr_number, exc)

    # ── 👍 reaction on PR ────────────────────────────────────────────────────
    try:
        reactions = gh_client.get_pr_reactions(owner, repo, pr_number)
        human_reactions = [
            r for r in reactions
            if ((r.get("user") or {}).get("login") or "").lower() not in bot_logins
        ]
        approving = [
            r for r in human_reactions
            if not allowed or ((r.get("user") or {}).get("login") or "").lower() in allowed
        ]
        if gh_client.has_thumbs_up(approving):
            logger.info("pr_review_watcher: PR #%d approved via 👍 reaction", pr_number)
            _merge_and_done(state, state_path, pr_data, gh_client, owner, repo, settings, reason="thumbs_up_reaction")
            return
    except Exception as exc:
        logger.debug("pr_review_watcher: reaction fetch failed PR #%d — %s", pr_number, exc)

    # ── Poll comments ────────────────────────────────────────────────────────
    try:
        all_comments = gh_client.list_pr_comments(owner, repo, pr_number)
    except Exception as exc:
        logger.warning("pr_review_watcher: comment fetch failed PR #%d — %s", pr_number, exc)
        return

    processed_ids: set[int] = set(state.get("processed_comment_ids", []))
    human_comments = []

    for c in all_comments:
        cid = c.get("id")
        if cid in processed_ids:
            continue
        if _is_bot_comment(c, bot_logins, reviewer.bot_comment_marker):
            processed_ids.add(cid)
            continue
        login = ((c.get("user") or {}).get("login") or "").lower()
        if allowed and login not in allowed:
            continue
        human_comments.append(c)

    state["processed_comment_ids"] = list(processed_ids)

    if not human_comments:
        _save_state(state_path, state)
        return

    # /lgtm in any unprocessed human comment
    for c in human_comments:
        if _is_lgtm_comment(c):
            logger.info("pr_review_watcher: PR #%d approved via /lgtm", pr_number)
            for hc in human_comments:
                processed_ids.add(hc.get("id"))
            state["processed_comment_ids"] = list(processed_ids)
            _save_state(state_path, state)
            _merge_and_done(state, state_path, pr_data, gh_client, owner, repo, settings, reason="lgtm_comment")
            return

    # Max loops reached → auto-merge rather than looping forever
    if state["human_review_loops"] >= reviewer.max_human_review_loops:
        logger.info("pr_review_watcher: PR #%d max human review loops reached, auto-merging", pr_number)
        notice = (
            f"{reviewer.bot_comment_marker}\n"
            f"**Auto-merging** — reached max human review loops ({reviewer.max_human_review_loops})."
        )
        try:
            gh_client.post_comment(owner, repo, pr_number, notice)
        except Exception:
            pass
        _merge_and_done(state, state_path, pr_data, gh_client, owner, repo, settings, reason="max_human_loops")
        return

    # Revision pass for newest human comment
    newest      = human_comments[-1]
    comment_body = newest.get("body", "")
    commenter    = ((newest.get("user") or {}).get("login") or "unknown")

    logger.info(
        "pr_review_watcher: PR #%d revision pass %d for comment from %s",
        pr_number, state["human_review_loops"], commenter,
    )

    goal_text = (
        f"Address the following reviewer feedback on PR #{pr_number}: {pr_data.get('title', '')}\n\n"
        f"Reviewer ({commenter}) wrote:\n{comment_body}\n\n"
        f"Make the requested changes. When done, write `verdict.json` in the current directory:\n"
        f'{{"result": "CONCERNS_ADDRESSED", "summary": "brief description of what changed"}}'
    )

    verdict = _run_pipeline(
        oc_root, config_path, repo_key, goal_text, settings,
        source="reviewer_human",
        state_key=state_key,
        branch_suffix=f"{state_key[:12]}-h{state['human_review_loops']}",
    )

    for hc in human_comments:
        processed_ids.add(hc.get("id"))

    state["human_review_loops"]    += 1
    state["processed_comment_ids"] = list(processed_ids)

    summary = (verdict or {}).get("summary", "revision complete — please re-review")
    reply   = f"{reviewer.bot_comment_marker}\nChanges applied: {summary}"
    try:
        gh_client.post_comment(owner, repo, pr_number, reply)
    except Exception as exc:
        logger.warning("pr_review_watcher: failed to post revision reply PR #%d — %s", pr_number, exc)

    _save_state(state_path, state)


# ── Plane task lookup ─────────────────────────────────────────────────────────

def _find_plane_task_id(settings, repo_key: str, pr_number: int, pr_data: dict) -> str | None:
    """Attempt to find a Plane 'In Review' task matching this PR. Best-effort."""
    try:
        client = _plane_client(settings)
        try:
            issues = client.list_issues()
        finally:
            client.close()
        for issue in issues:
            state_obj  = issue.get("state")
            state_name = (state_obj.get("name", "") if isinstance(state_obj, dict) else "").strip()
            if state_name != "In Review":
                continue
            labels = issue.get("labels", [])
            if _label_value(labels, "repo") != repo_key:
                continue
            desc = issue.get("description") or issue.get("description_stripped") or ""
            if f"#{pr_number}" in desc or f"/{pr_number}" in desc:
                return str(issue["id"])
    except Exception as exc:
        logger.debug("pr_review_watcher: Plane task lookup failed — %s", exc)
    return None


# ── Heartbeat ──────────────────────────────────────────────────────────────────

def _write_heartbeat(status_dir: Path) -> None:
    try:
        status_dir.mkdir(parents=True, exist_ok=True)
        hb = status_dir / "heartbeat_review.json"
        hb.write_text(json.dumps({
            "role":   "review",
            "at":     datetime.now(UTC).isoformat(),
            "status": "active",
        }), encoding="utf-8")
    except Exception:
        pass


# ── Poll cycle ────────────────────────────────────────────────────────────────

def _poll_once(oc_root: Path, config_path: Path, settings) -> None:
    gh_client = _github_client(settings)

    repos_to_watch = {
        key: repo
        for key, repo in settings.repos.items()
        if repo.await_review
    }

    if not repos_to_watch:
        logger.debug("pr_review_watcher: no repos with await_review=true, nothing to do")
        return

    for repo_key, repo_cfg in repos_to_watch.items():
        try:
            owner, repo = _owner_repo(repo_cfg.clone_url)
        except Exception as exc:
            logger.warning("pr_review_watcher: bad clone_url for %s — %s", repo_key, exc)
            continue

        try:
            open_prs = gh_client.list_open_prs(owner, repo)
        except Exception as exc:
            logger.warning("pr_review_watcher: failed to list PRs %s/%s — %s", owner, repo, exc)
            continue

        for pr_data in open_prs:
            if pr_data.get("draft"):
                continue

            pr_number = int(pr_data["number"])
            sp        = _state_path(oc_root, repo_key, pr_number)

            if not sp.exists():
                state = _new_state(repo_key, pr_number)
                state["plane_task_id"] = _find_plane_task_id(settings, repo_key, pr_number, pr_data)
                _save_state(sp, state)
                logger.info("pr_review_watcher: discovered PR #%d repo=%s", pr_number, repo_key)

            state = _load_state(sp)
            if not state:
                continue

            phase = state.get("phase", "self_review")

            if phase == "self_review":
                _phase1(state, sp, pr_data, gh_client, owner, repo, oc_root, config_path, settings)
            elif phase == "human_review":
                _phase2(state, sp, pr_data, gh_client, owner, repo, oc_root, config_path, settings)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="OperationsCenter PR review watcher — two-phase state machine"
    )
    parser.add_argument("--config",                  required=True, type=Path)
    parser.add_argument("--watch",                   action="store_true")
    parser.add_argument("--poll-interval-seconds",   type=int, default=60, dest="poll_interval")
    parser.add_argument("--status-dir",              type=Path, default=None, dest="status_dir")
    parser.add_argument("--log-level",               default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [review] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    oc_root    = Path(__file__).resolve().parents[4]
    status_dir = args.status_dir or (oc_root / "logs" / "local" / "watch-all")

    if not args.watch:
        try:
            settings = _load_settings(args.config)
            _poll_once(oc_root, args.config, settings)
        except Exception as exc:
            logger.error("pr_review_watcher: error — %s", exc, exc_info=True)
            return 1
        _write_heartbeat(status_dir)
        return 0

    logger.info("pr_review_watcher: starting — poll_interval=%ds", args.poll_interval)
    while True:
        try:
            settings = _load_settings(args.config)
            _poll_once(oc_root, args.config, settings)
        except Exception as exc:
            logger.error("pr_review_watcher: unhandled error — %s", exc, exc_info=True)
        _write_heartbeat(status_dir)
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
