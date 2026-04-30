# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Board worker — Plane-polling watcher for goal, test, and improve roles.

Polls the Plane board for "Ready for AI" issues with a matching task-kind label,
claims one, drives the planning → execution pipeline (identical to intake), then
transitions board state and creates follow-up tasks per the lifecycle contract.

Each role runs as a separate process. The shell launcher passes:
    --config              path to operations_center.local.yaml
    --role                goal | test | improve
    --poll-interval-seconds N
    --status-dir          directory for heartbeat_{role}.json

Task-kind label mapping:
    goal    → task-kind: goal
    test    → task-kind: test  OR  task-kind: test_campaign
    improve → task-kind: improve  OR  task-kind: improve_campaign

Follow-up creation per lifecycle contract:
    goal success + verification needed → creates task-kind: test (Ready for AI)
    goal success + no verification     → transitions to Review (or Done)
    goal failure                       → transitions to Blocked
    test success                       → transitions to Done
    test failure                       → creates task-kind: goal (Ready for AI)
    improve any outcome                → creates bounded follow-up or Blocked
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

_GITHUB_DIR = Path.home() / "Documents" / "GitHub"

# Plane states
_STATE_READY      = "Ready for AI"
_STATE_RUNNING    = "Running"
_STATE_DONE       = "Done"
_STATE_BLOCKED    = "Blocked"
_STATE_REVIEW     = "In Review"

# Lifecycle marker — applied to a task whose work has been delegated to
# spawned children (scope-split, future decomposition modes). Any service
# that re-processes Blocked tasks (spec_director's blocked-rewrite, the
# auto-promote loop, future recovery services) must skip tasks carrying
# this label so we don't generate ghost work on a meta-task whose real
# work is already happening downstream.
_LIFECYCLE_EXPANDED = "lifecycle: expanded"

# task-kind labels claimed per role
_ROLE_KINDS: dict[str, list[str]] = {
    "goal":    ["goal"],
    "test":    ["test", "test_campaign"],
    "improve": ["improve", "improve_campaign"],
}


# ── Plane helpers ─────────────────────────────────────────────────────────────

def _label_value(labels: list, prefix: str) -> str:
    """Extract value from a 'prefix: value' label, or ''."""
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


def _has_label(labels: list, value: str) -> bool:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower() == value.lower():
            return True
    return False


def _load_settings(config_path: Path):
    from operations_center.config import load_settings
    return load_settings(config_path)


def _plane_client(settings):
    from operations_center.adapters.plane import PlaneClient
    return PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )


def _repo_local_path(settings, repo_key: str) -> str:
    repo = settings.repos.get(repo_key)
    if repo and repo.local_path:
        return repo.local_path
    return str(_GITHUB_DIR / repo_key)


def _claim_next(client, role: str, settings) -> dict | None:
    """
    Find the oldest Ready-for-AI issue matching this role's task-kinds and a
    known repo. Immediately transition to Running to claim it.
    Returns the raw Plane issue dict, or None if nothing is available.
    """
    kinds = _ROLE_KINDS[role]
    managed_repos = set(settings.repos.keys())

    try:
        issues = client.list_issues()
    except Exception:
        logger.warning("board_worker[%s]: failed to list issues", role)
        return None

    # Daily-execution counter per repo: count tasks the worker has touched
    # today (anything currently in Running / In Review / Done / Blocked
    # whose updated_at is within the last 24h). Used to enforce
    # RepoSettings.max_daily_executions.
    _exec_today: dict[str, int] = {}
    _now_utc = datetime.now(UTC)
    _touched_states = {"running", "in review", "done", "blocked"}
    for issue in issues:
        st = issue.get("state")
        st_name = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip().lower()
        if st_name not in _touched_states:
            continue
        ts_raw = issue.get("updated_at") or ""
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        if (_now_utc - ts).total_seconds() > 86400:
            continue
        rk = _label_value(issue.get("labels", []), "repo")
        if rk:
            _exec_today[rk] = _exec_today.get(rk, 0) + 1

    candidates = []
    for issue in issues:
        state_obj = issue.get("state")
        state_name = (state_obj.get("name", "") if isinstance(state_obj, dict) else str(state_obj or "")).strip()
        if state_name != _STATE_READY:
            continue
        labels = issue.get("labels", [])
        task_kind = _label_value(labels, "task-kind")
        if task_kind not in kinds:
            continue
        repo_key = _label_value(labels, "repo")
        if repo_key not in managed_repos:
            continue
        # Per-repo quota gate. RepoSettings.max_daily_executions is None /
        # 0 by default (no cap); a positive int enforces a daily ceiling
        # against the count we computed above.
        repo_cfg = settings.repos.get(repo_key)
        cap = getattr(repo_cfg, "max_daily_executions", None) if repo_cfg else None
        if cap and _exec_today.get(repo_key, 0) >= int(cap):
            logger.info(
                "board_worker[%s]: skipping repo %s — daily quota %d reached (today=%d)",
                role, repo_key, cap, _exec_today[repo_key],
            )
            continue
        candidates.append(issue)

    if not candidates:
        return None

    # Priority order:
    #   1. improve-suggestions first — they represent recent partially-complete
    #      analysis that someone (kodo) just identified as worth doing. Picking
    #      them up while the context is fresh keeps related changes coherent
    #      and prevents stale suggestions piling up at the bottom of the queue.
    #   2. then by Plane priority field if set (urgent, high, medium, low, none)
    #   3. then by created_at — oldest first as a stable tiebreaker.
    _PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
    def _sort_key(issue: dict) -> tuple:
        labs = [
            (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            for lab in issue.get("labels", [])
        ]
        is_improve_suggestion = 0 if "source: improve-suggestion" in labs else 1
        plane_priority = str(issue.get("priority") or "none").lower()
        plane_rank = _PRIORITY_ORDER.get(plane_priority, 4)
        return (is_improve_suggestion, plane_rank, issue.get("created_at", ""))

    candidates.sort(key=_sort_key)
    issue = candidates[0]

    # Ghost-work guard G7: skip tasks whose goal text is too thin for kodo to
    # do anything meaningful. A 16-minute run on an empty description is pure
    # quota-burn. We mark the task Blocked with a clear reason so the operator
    # (or spec_director) can fill in details and re-promote.
    desc = issue.get("description") or issue.get("description_stripped") or ""
    title = issue.get("name", "")
    candidate_goal = _extract_goal(desc, title).strip()
    _MIN_GOAL_TEXT_CHARS = 40
    if len(candidate_goal) < _MIN_GOAL_TEXT_CHARS:
        try:
            client.transition_issue(str(issue["id"]), _STATE_BLOCKED)
            client.comment_issue(
                str(issue["id"]),
                f"board_worker[{role}] refused to claim — goal text too thin "
                f"({len(candidate_goal)} chars; minimum {_MIN_GOAL_TEXT_CHARS}). "
                f"Add concrete description and re-promote to Ready for AI.",
            )
        except Exception as exc:
            logger.warning("board_worker[%s]: empty-goal block failed task_id=%s — %s",
                            role, issue.get("id"), exc)
        logger.info(
            "board_worker[%s]: refused thin task_id=%s title=%r",
            role, issue.get("id"), title,
        )
        return None
    task_id = str(issue["id"])

    try:
        client.transition_issue(task_id, _STATE_RUNNING)
        logger.info("board_worker[%s]: claimed task_id=%s title=%r", role, task_id, issue.get("name", ""))
    except Exception as exc:
        logger.warning("board_worker[%s]: failed to claim task_id=%s — %s", role, task_id, exc)
        return None

    return issue


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _build_env(oc_root: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(oc_root / "src")
    return env


def _venv_python(oc_root: Path) -> str:
    p = oc_root / ".venv" / "bin" / "python"
    return str(p) if p.exists() else "python3"


_TRANSIENT_CATEGORIES = {"backend_error", "timeout"}
_TRANSIENT_REASON_PATTERNS = (
    "connection refused", "connection reset", "timed out", "timeout",
    "502", "503", "504", "bad gateway", "gateway timeout", "service unavailable",
    "remote disconnected", "network is unreachable", "temporary failure",
)


def _is_transient_failure(result: dict) -> bool:
    """Return True when an execution failure looks like a transient blip.

    Conservative match: requires category to be backend_error or timeout
    AND the reason text to contain a network-shaped phrase. Avoids
    over-retrying genuine bugs (which surface as backend_error too but
    with a Python traceback in the reason).
    """
    cat = (result.get("failure_category") or "").lower()
    if cat not in _TRANSIENT_CATEGORIES:
        return False
    reason = (result.get("failure_reason") or "").lower()
    return any(p in reason for p in _TRANSIENT_REASON_PATTERNS)


def _process_issue(issue: dict, role: str, config_path: Path, settings, client) -> bool:
    """
    Drive one claimed Plane issue through planning → execution.
    Transitions board state and creates follow-ups on completion.
    Returns True on success.
    """
    task_id   = str(issue["id"])
    title     = issue.get("name", "Untitled")
    labels    = issue.get("labels", [])
    repo_key  = _label_value(labels, "repo")
    task_kind = _label_value(labels, "task-kind")

    description = issue.get("description") or issue.get("description_stripped") or ""

    # Extract goal text from description
    goal_text = _extract_goal(description, title)

    # Rejection-pattern hint: if recent proposals in this repo were rejected
    # for a recurring reason, prepend a "common rejection patterns to avoid"
    # block to the goal text. Best-effort — no hint when the catalog is
    # empty or unreadable. Helps kodo avoid the same mistake twice.
    try:
        from operations_center.quality_alerts import _load_rejection_patterns_for_proposal
        patterns = _load_rejection_patterns_for_proposal(repo_key=repo_key)
        if patterns:
            goal_text = (
                f"{goal_text}\n\n"
                f"## Rejection patterns to avoid\n"
                f"Recent proposals in this repo were rejected for these reasons; "
                f"do not repeat them:\n"
                + "\n".join(f"- {p}" for p in patterns[:5])
            )
    except Exception:
        pass

    # Improve mode: ask kodo to emit structured suggestions we can turn into
    # concrete follow-up tasks for the propose lane. Without this prompt, the
    # improve run is a pure-side-effect analysis that produces no downstream
    # signal — the duplicate-PR problem we saw with PR #55/#60.
    if role == "improve":
        goal_text = (
            f"{goal_text}\n\n"
            f"## Output\n"
            f"Write your analysis to `improve-output.json` in the project root with:\n"
            f"```json\n"
            f"{{\n"
            f'  "summary": "1-2 sentence high-level finding",\n'
            f'  "suggestions": [\n'
            f'    {{"title": "concrete actionable change",\n'
            f'      "rationale": "why this matters",\n'
            f'      "files": ["path/to/file"],\n'
            f'      "complexity": "small|medium|large"}}\n'
            f"  ]\n"
            f"}}\n"
            f"```\n"
            f"Each suggestion should be small enough to implement in a focused PR "
            f"(complexity:small ≈ <50 LOC, medium ≈ <200 LOC, large flagged for split). "
            f"Limit to 5 suggestions; pick the highest-impact ones."
        )

    # Derive execution_mode from task_kind (test_campaign → test_campaign, etc.)
    execution_mode = task_kind if task_kind in {"goal", "test_campaign", "improve_campaign"} else task_kind

    repo_path  = _repo_local_path(settings, repo_key)
    repo_cfg   = settings.repos.get(repo_key)
    clone_url  = repo_cfg.clone_url if repo_cfg else f"file://{repo_path}"
    # Precedence for base_branch:
    #   1. explicit per-task override label "base-branch: X"
    #   2. repo's sandbox_base_branch (autonomy work targets staging, not main)
    #   3. repo's default_branch
    base_branch = (
        _label_value(labels, "base-branch")
        or (repo_cfg.sandbox_base_branch if repo_cfg and repo_cfg.sandbox_base_branch else None)
        or (repo_cfg.default_branch if repo_cfg else "main")
    )

    oc_root  = Path(__file__).resolve().parents[4]
    python   = _venv_python(oc_root)
    env      = _build_env(oc_root)
    short_id = task_id[:8]

    logger.info(
        "board_worker[%s]: processing task_id=%s repo=%s kind=%s",
        role, task_id, repo_key, task_kind,
    )

    with tempfile.TemporaryDirectory(prefix=f"oc-{role}-") as tmpdir:
        tmp = Path(tmpdir)

        # ── Step 1: Planning ──────────────────────────────────────────────
        # Forward source labels so the policy engine can recognise pre-authorised
        # lanes (autonomy tier, spec campaigns) and skip its review-by-task-type
        # check. Without this, every goal/improve task gets policy-blocked.
        forwarded_labels: list[str] = []
        # C-K6: when the repo opts in via require_explicit_approval, we DO
        # NOT forward trusted-source labels — every task on that repo must
        # pass the full review gate even if its label set would otherwise
        # qualify for the bypass. Per-repo override of the trust default.
        explicit_required = bool(getattr(repo_cfg, "require_explicit_approval", False))
        for label in labels:
            name = (label.get("name", "") if isinstance(label, dict) else str(label)).strip()
            low = name.lower()
            if low == "review_required":
                forwarded_labels.append(name)
                continue
            if low.startswith("source:"):
                if explicit_required and low in {
                    "source: autonomy", "source: spec-campaign", "source: board_worker",
                }:
                    # Drop the trusted-source label so policy treats the
                    # task as untrusted. The original Plane labels stay on
                    # the issue itself — only the proposal carries the
                    # filtered set.
                    continue
                forwarded_labels.append(name)
        if explicit_required:
            # Tag the proposal so policy explicitly requires review even
            # when no other rule would fire (defence in depth).
            forwarded_labels.append("review_required")

        plan_cmd = [
            python, "-m", "operations_center.entrypoints.worker.main",
            "--goal",           goal_text,
            "--task-type",      _task_type_from_kind(task_kind),
            "--execution-mode", execution_mode,
            "--repo-key",       repo_key,
            "--clone-url",      clone_url,
            "--base-branch",    base_branch,
            "--project-id",     settings.plane.project_id,
            "--task-id",        task_id,
        ]
        for lbl in forwarded_labels:
            plan_cmd.extend(["--label", lbl])

        plan_proc = subprocess.run(
            plan_cmd, cwd=oc_root, env=env, capture_output=True, text=True,
        )

        try:
            bundle = json.loads(plan_proc.stdout)
        except Exception:
            logger.error(
                "board_worker[%s]: planning produced no JSON for task_id=%s\n%s",
                role, task_id, plan_proc.stderr.strip() or plan_proc.stdout.strip(),
            )
            _fail_task(client, task_id, role, "planning produced no JSON output")
            return False

        if plan_proc.returncode != 0:
            msg = bundle.get("message", "unknown planning error")
            logger.error("board_worker[%s]: planning failed for task_id=%s — %s", role, task_id, msg)
            _fail_task(client, task_id, role, f"planning failed: {msg}")
            return False

        # ── Step 2: Execution ─────────────────────────────────────────────
        bundle_file = tmp / "bundle.json"
        bundle_file.write_text(json.dumps(bundle), encoding="utf-8")

        config_file = tmp / "ops.yaml"
        shutil.copy(config_path, config_file)

        workspace = tmp / "workspace"
        workspace.mkdir()
        result_file = tmp / "result.json"

        exec_cmd = [
            python, "-m", "operations_center.entrypoints.execute.main",
            "--config",         str(config_file),
            "--bundle",         str(bundle_file),
            "--workspace-path", str(workspace),
            "--task-branch",    f"{role}/{short_id}",
            "--output",         str(result_file),
            "--source",         f"board_worker_{role}",
        ]

        subprocess.run(exec_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        if not result_file.exists():
            logger.error("board_worker[%s]: execute produced no result for task_id=%s", role, task_id)
            _fail_task(client, task_id, role, "execute produced no result file")
            return False

        outcome = json.loads(result_file.read_text(encoding="utf-8"))
        result  = outcome.get("result", {})
        success = result.get("success", False)
        status  = result.get("status", "unknown")
        needs_verification = result.get("needs_verification", False)

        # D1: transient kodo retry. Network blips and 502s shouldn't sink a
        # task — they're not authoritative outcomes. Detect by failure
        # category + reason shape; retry once with a fresh workspace before
        # giving up. Capped at 1 to avoid infinite loops.
        if (not success
            and _is_transient_failure(result)
            and not outcome.get("retried")):
            logger.info(
                "board_worker[%s]: task_id=%s transient failure (%s) — retrying once",
                role, task_id, result.get("failure_reason", "")[:80],
            )
            # Fresh workspace for the retry — the previous one may have a
            # half-clone or partial commit state.
            shutil.rmtree(workspace, ignore_errors=True)
            workspace.mkdir()
            retry_result_file = tmp / "result.retry.json"
            retry_cmd = list(exec_cmd)
            retry_cmd[retry_cmd.index("--output") + 1] = str(retry_result_file)
            retry_cmd[retry_cmd.index("--source")  + 1] = f"board_worker_{role}_retry"
            subprocess.run(retry_cmd, cwd=oc_root, env=env, capture_output=True, text=True)
            if retry_result_file.exists():
                outcome = json.loads(retry_result_file.read_text(encoding="utf-8"))
                outcome["retried"] = True
                result  = outcome.get("result", {})
                success = result.get("success", False)
                status  = result.get("status", "unknown")
                needs_verification = result.get("needs_verification", False)
                # Persist the new outcome for downstream readers (artifact
                # writer, observability) — overwrite original so retry is
                # the recorded outcome, not the transient blip.
                result_file.write_text(json.dumps(outcome), encoding="utf-8")

        # Improve mode: harvest structured suggestions from kodo's workspace
        # before the tempdir is cleaned. _handle_success uses these to spawn
        # focused Plane tasks that the propose lane can refine and prioritise.
        improve_suggestions: list[dict] = []
        if role == "improve" and success:
            improve_suggestions = _read_improve_output(workspace)

        # Scope-too-wide: WorkspaceManager wrote scope-too-wide.json with the
        # file list. Read it before tempdir cleanup so _handle_failure can
        # spawn focused split tasks.
        scope_files: list[str] = []
        scope_file = workspace / "scope-too-wide.json"
        if scope_file.exists():
            try:
                scope_files = json.loads(scope_file.read_text(encoding="utf-8")).get("files") or []
            except Exception:
                pass

        # The kodo run can succeed but produce nothing shippable — e.g. the
        # workspace's diff exceeded the soft cap and WorkspaceManager refused
        # to push. In that case we want the task to be Blocked with the
        # actionable reason, not silently moved to In Review with no PR.
        scope_too_wide = (
            success
            and result.get("branch_pushed") is False
            and result.get("failure_category") == "scope_too_wide"
        )

        if success and not scope_too_wide:
            logger.info("board_worker[%s]: task_id=%s completed status=%s", role, task_id, status)
            _handle_success(
                client, issue, role, task_kind, needs_verification, settings,
                improve_suggestions=improve_suggestions,
            )
        else:
            log_reason = "scope_too_wide" if scope_too_wide else status
            logger.warning("board_worker[%s]: task_id=%s failed status=%s", role, task_id, log_reason)
            _handle_failure(
                client, issue, role, task_kind, result, settings,
                scope_files=scope_files if scope_too_wide else [],
            )

        return success and not scope_too_wide


# ── Outcome handlers ──────────────────────────────────────────────────────────

def _fail_task(client, task_id: str, role: str, reason: str) -> None:
    try:
        client.transition_issue(task_id, _STATE_BLOCKED)
        client.comment_issue(task_id, f"board_worker[{role}] blocked — {reason}")
    except Exception as exc:
        logger.warning("board_worker[%s]: failed to mark task_id=%s blocked — %s", role, task_id, exc)


def _read_improve_output(workspace: Path) -> list[dict]:
    """Pull structured suggestions written by kodo to improve-output.json.

    Returns [] when the file is missing or malformed — a missing output is
    common when kodo improve mode runs against a healthy module and finds
    nothing actionable. Callers treat that as "no follow-up tasks needed".
    """
    out_file = workspace / "improve-output.json"
    if not out_file.exists():
        return []
    try:
        data = json.loads(out_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("board_worker[improve]: malformed improve-output.json — %s", exc)
        return []
    raw = data.get("suggestions") or []
    if not isinstance(raw, list):
        return []
    valid = []
    for item in raw[:5]:  # cap at 5 — same limit we asked kodo for
        if isinstance(item, dict) and item.get("title"):
            valid.append(item)
    return valid


def _handle_success(client, issue: dict, role: str, task_kind: str, needs_verification: bool, settings,
                    *, improve_suggestions: list[dict] | None = None) -> None:
    task_id = str(issue["id"])
    labels  = issue.get("labels", [])
    repo_key = _label_value(labels, "repo")
    await_review = (settings.repos.get(repo_key) and settings.repos[repo_key].await_review) if repo_key else False

    try:
        if role == "goal":
            if needs_verification:
                follow_id = _create_follow_up(client, issue, settings, follow_kind="test",
                                               reason="verification_needed")
                client.comment_issue(task_id,
                    f"Implementation complete — created verification task #{follow_id}")
                client.transition_issue(task_id, _STATE_DONE)
            elif await_review:
                client.transition_issue(task_id, _STATE_REVIEW)
                client.comment_issue(task_id, "Implementation complete — moved to In Review")
            else:
                client.transition_issue(task_id, _STATE_DONE)
                client.comment_issue(task_id, "Implementation complete")

        elif role == "test":
            client.transition_issue(task_id, _STATE_DONE)
            client.comment_issue(task_id, "Verification passed")

        elif role == "improve":
            # Improve is analysis-only. Instead of mirroring the parent title
            # as a "follow-up goal" (the duplicate-PR problem), we read the
            # structured suggestions kodo wrote to improve-output.json and
            # create one focused goal task per suggestion. The propose lane
            # picks them up like any other autonomy work.
            client.transition_issue(task_id, _STATE_DONE)
            if improve_suggestions:
                created_ids = []
                for suggestion in improve_suggestions:
                    follow_id = _create_improve_follow_up(
                        client, issue, settings, suggestion,
                    )
                    if follow_id:
                        created_ids.append(follow_id)
                client.comment_issue(
                    task_id,
                    f"Improvement analysis complete — created {len(created_ids)} "
                    f"focused follow-up task(s): {', '.join('#' + i for i in created_ids)}"
                    if created_ids
                    else "Improvement analysis complete — kodo wrote suggestions but none could be enqueued",
                )
            else:
                client.comment_issue(
                    task_id,
                    "Improvement analysis complete — no actionable suggestions emitted "
                    "(kodo found nothing concrete, or improve-output.json was missing)",
                )

    except Exception as exc:
        logger.warning("board_worker[%s]: post-success transition failed task_id=%s — %s", role, task_id, exc)

    # If this task is a scope-split child, check whether all siblings are now
    # Done — if so, close the parent that's been sitting in Blocked since the
    # split fired. Without this hook the parent never reaches a clean
    # terminal state even after all the work it represents has shipped.
    try:
        _maybe_close_split_parent(client, issue)
    except Exception as exc:
        logger.warning("board_worker: close-parent check failed for task_id=%s — %s", task_id, exc)


def _maybe_close_split_parent(client, completed_issue: dict) -> None:
    """Close the parent of a scope-split when its last child completes.

    No-op when the just-completed task isn't a scope-split child, the parent
    can't be found, the parent isn't Blocked (already terminal in some way),
    or any sibling besides this one is still pending.
    """
    labels = completed_issue.get("labels", [])
    label_names_lower = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        for lab in labels
    ]
    if "source: scope-split" not in label_names_lower:
        return
    parent_id = _label_value(labels, "original-task-id")
    if not parent_id:
        return

    try:
        all_issues = client.list_issues()
    except Exception:
        return

    this_task_id = str(completed_issue.get("id", ""))
    parent: dict | None = None
    siblings: list[dict] = []
    for iss in all_issues:
        iss_id = str(iss.get("id", ""))
        if iss_id == parent_id:
            parent = iss
            continue
        if _label_value(iss.get("labels", []), "original-task-id") == parent_id:
            # Restrict to scope-split siblings — other follow-ups (improve
            # suggestions etc.) may share the parent_id and shouldn't gate
            # closure.
            sib_labels_lower = [
                (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
                for lab in iss.get("labels", [])
            ]
            if "source: scope-split" in sib_labels_lower:
                siblings.append(iss)

    if parent is None:
        return
    parent_state = (parent.get("state") or {}).get("name", "")
    if parent_state.lower() != "blocked":
        return  # Already closed, or in some other state we shouldn't touch

    # Every other split sibling must already be in Done (the just-completed
    # one we treat as Done since we just transitioned it; but we still verify
    # via Plane state to be defensive against a missed transition).
    other = [s for s in siblings if str(s.get("id", "")) != this_task_id]
    other_done = all(
        (s.get("state") or {}).get("name", "").strip().lower() == "done"
        for s in other
    )
    if not other_done:
        return
    # Verify this one too — defensive, in case the prior transition_issue
    # call lost the race somehow.
    this_state = (completed_issue.get("state") or {}).get("name", "").strip().lower()
    # `completed_issue` was hydrated *before* the success transition so it
    # may still show "running"; trust the side effect we just performed.
    if this_state and this_state not in ("done", "running", "ready for ai"):
        return

    n_total = len(siblings) + 1  # +1 for the just-completed task itself
    try:
        client.transition_issue(parent_id, _STATE_DONE)
        client.comment_issue(
            parent_id,
            f"Auto-closed: all {n_total} scope-split children completed.",
        )
        logger.info(
            "board_worker: closed parent task_id=%s after %d split children Done",
            parent_id, n_total,
        )
    except Exception as exc:
        logger.warning(
            "board_worker: failed to close parent task_id=%s — %s", parent_id, exc,
        )


def _split_files_into_chunks(files: list[str], chunk_size: int = 15, max_chunks: int = 6) -> list[list[str]]:
    """Group files into roughly-equal chunks, capped at max_chunks total.

    Files are first grouped by top-level directory (so related code stays
    together), then split into chunks. If grouping produces more than
    max_chunks, we merge the smallest groups together.
    """
    if not files:
        return []
    by_top: dict[str, list[str]] = {}
    for f in files:
        top = f.split("/", 1)[0] if "/" in f else "."
        by_top.setdefault(top, []).append(f)
    groups = sorted(by_top.values(), key=len, reverse=True)
    chunks: list[list[str]] = []
    for group in groups:
        for i in range(0, len(group), chunk_size):
            chunks.append(group[i : i + chunk_size])
    while len(chunks) > max_chunks and len(chunks) >= 2:
        # Merge the two smallest chunks until we're under the cap
        chunks.sort(key=len)
        merged = chunks[0] + chunks[1]
        chunks = [merged] + chunks[2:]
    return chunks


def _retry_count_from_labels(labels: list) -> int:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        if name.startswith("retry-count:"):
            try:
                return int(name.split(":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def _create_split_followups(client, parent: dict, settings, file_list: list[str], reason: str) -> list[str]:
    """Spawn smaller goal tasks scoped to file subsets after a scope_too_wide block.

    Caps total split depth at 2 (parent retry-count >= 2 → no further split,
    just block) so a confused kodo can't fork unboundedly.
    """
    parent_id     = str(parent["id"])
    parent_title  = parent.get("name", "")
    parent_labels = parent.get("labels", [])
    repo_key      = _label_value(parent_labels, "repo")
    retry_count   = _retry_count_from_labels(parent_labels)

    if retry_count >= 2:
        logger.info(
            "board_worker: not splitting task_id=%s — retry-count=%d already exhausted",
            parent_id, retry_count,
        )
        return []

    chunks = _split_files_into_chunks(file_list)
    if not chunks:
        return []

    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
    ]
    inherited_sources = [
        s for s in inherited_sources
        if s.lower().startswith("source:") and s.lower() != "source: board_worker"
    ]

    created: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        title = f"[split {idx}/{len(chunks)}] {parent_title}"[:80]
        files_block = "allowed_paths:\n" + "\n".join(f"  - {f}" for f in chunk) + "\n"
        description = (
            f"## Goal\n{parent_title}\n\n"
            f"This is split {idx} of {len(chunks)} from a scope_too_wide retry of "
            f"task #{parent_id}. Restrict changes to the listed files.\n\n"
            f"## Execution\n"
            f"repo: {repo_key}\n"
            f"mode: goal\n"
            f"{files_block}"
        )
        labels = [
            "task-kind: goal",
            f"repo: {repo_key}",
            "source: board_worker",
            "source: scope-split",
            *inherited_sources,
            f"original-task-id: {parent_id}",
            f"handoff-reason: {reason}",
            f"retry-count: {retry_count + 1}",
        ]
        try:
            new_issue = client.create_issue(
                name=title, description=description,
                state=_STATE_READY, label_names=labels,
            )
            new_id = str(new_issue.get("id", ""))
            if new_id:
                created.append(new_id)
        except Exception as exc:
            logger.warning("board_worker: split create_issue failed — %s", exc)
    # Mark the parent so triage / rewrite loops don't pick at it. Without
    # this, spec_director.phase_orchestrator._handle_blocked would later
    # call kodo to "rewrite" the parent description and re-queue it,
    # producing exactly the kind of ghost work this audit is trying to
    # eliminate.
    if created:
        _add_label(client, parent, _LIFECYCLE_EXPANDED)

    logger.info(
        "board_worker: split task_id=%s into %d chunks (retry-count=%d → %d)",
        parent_id, len(created), retry_count, retry_count + 1,
    )
    return created


def _add_label(client, issue: dict, new_label: str) -> None:
    """Append `new_label` to an issue's label set if not already present.

    Plane's update_issue_labels replaces the set, so we read existing
    labels first. Failures are non-fatal — at worst the parent stays
    un-marked and a downstream service might re-process; the next cycle
    of board_worker can re-apply.
    """
    existing = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in issue.get("labels", [])
    ]
    existing = [name for name in existing if name]
    if new_label in existing:
        return
    try:
        client.update_issue_labels(str(issue["id"]), existing + [new_label])
    except Exception as exc:
        logger.warning(
            "board_worker: failed to add label %r to task_id=%s — %s",
            new_label, issue.get("id"), exc,
        )


def _handle_failure(
    client, issue: dict, role: str, task_kind: str, result: dict, settings,
    *, scope_files: list[str] | None = None,
) -> None:
    task_id  = str(issue["id"])
    status   = result.get("status", "unknown")
    category = result.get("failure_category") or "unknown"
    reason   = result.get("failure_reason") or "(no reason provided)"

    # Scope-too-wide auto-recovery: if we have the file list, spawn focused
    # split tasks instead of leaving the operator to do it manually. The
    # parent task itself still moves to Blocked so the original entry has a
    # clean terminal state.
    split_ids: list[str] = []
    if category == "scope_too_wide" and scope_files:
        try:
            split_ids = _create_split_followups(
                client, issue, settings, scope_files, reason="scope_too_wide_split",
            )
        except Exception as exc:
            logger.warning("board_worker: scope-split spawn failed — %s", exc)

    # Log the full reason — operators want to see this in the worker logs even
    # when the Plane comment is truncated.
    logger.warning(
        "board_worker[%s]: task_id=%s blocked status=%s category=%s reason=%s",
        role, task_id, status, category, reason,
    )

    try:
        if role == "test":
            follow_id = _create_follow_up(client, issue, settings, follow_kind="goal",
                                           reason="verification_failed")
            client.transition_issue(task_id, _STATE_BLOCKED)
            client.comment_issue(
                task_id,
                f"Verification failed — created follow-up goal task #{follow_id}\n"
                f"\n"
                f"- status: {status}\n"
                f"- category: {category}\n"
                f"- reason: {reason}",
            )
        else:
            client.transition_issue(task_id, _STATE_BLOCKED)
            split_block = ""
            if split_ids:
                split_block = f"\n\nAuto-split into {len(split_ids)} focused task(s): {', '.join('#' + i for i in split_ids)}"
            client.comment_issue(
                task_id,
                f"board_worker[{role}] failed\n"
                f"\n"
                f"- status: {status}\n"
                f"- category: {category}\n"
                f"- reason: {reason}"
                f"{split_block}",
            )
    except Exception as exc:
        logger.warning("board_worker[%s]: post-failure transition failed task_id=%s — %s", role, task_id, exc)


def _create_improve_follow_up(
    client, parent: dict, settings, suggestion: dict,
) -> str | None:
    """Create a focused goal task from one kodo improve suggestion.

    Carries forward the parent's repo + source provenance, embeds the
    suggestion's rationale and file scope into the task description so kodo's
    next run has concrete context. Returns the new task id, or None on error.
    """
    parent_id     = str(parent["id"])
    parent_labels = parent.get("labels", [])
    repo_key      = _label_value(parent_labels, "repo")

    title       = str(suggestion.get("title", "")).strip()[:80] or "Improve follow-up"
    rationale   = str(suggestion.get("rationale", "")).strip()
    files       = suggestion.get("files") or []
    complexity  = str(suggestion.get("complexity", "")).strip().lower()

    files_block = ""
    if isinstance(files, list) and files:
        files_block = "allowed_paths:\n" + "\n".join(f"  - {f}" for f in files[:10] if isinstance(f, str)) + "\n"

    description = (
        f"## Goal\n{title}\n\n"
        f"## Rationale\n{rationale or '(none provided)'}\n\n"
        f"## Execution\n"
        f"repo: {repo_key}\n"
        f"mode: goal\n"
        f"{files_block}"
    )

    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
    ]
    inherited_sources = [
        s for s in inherited_sources
        if s.lower().startswith("source:") and s.lower() != "source: board_worker"
    ]
    label_names = [
        "task-kind: goal",
        f"repo: {repo_key}",
        "source: board_worker",
        "source: improve-suggestion",  # distinct so the propose lane can recognise it
        *inherited_sources,
        f"original-task-id: {parent_id}",
        "handoff-reason: improve_suggestion",
    ]
    if complexity in {"small", "medium", "large"}:
        label_names.append(f"complexity: {complexity}")

    try:
        issue = client.create_issue(
            name=title,
            description=description,
            state=_STATE_READY,
            label_names=label_names,
        )
        new_id = str(issue.get("id", ""))
        logger.info(
            "board_worker[improve]: spawned follow-up task_id=%s title=%r complexity=%s",
            new_id, title, complexity,
        )
        return new_id or None
    except Exception as exc:
        logger.warning(
            "board_worker[improve]: failed to create follow-up for %r — %s",
            title, exc,
        )
        return None


_MAX_FOLLOW_UP_RETRIES = 3


def _create_follow_up(client, parent: dict, settings, follow_kind: str, reason: str) -> str:
    """Create a follow-up Plane task with full lineage metadata. Returns the new task id.

    Refuses past `_MAX_FOLLOW_UP_RETRIES` (default 3): a chain of test_failure →
    goal → test_failure → goal would otherwise burn quota on the same goal
    text indefinitely. When the cap is reached, the parent is left in its
    current state and we return "" so the caller can short-circuit.
    """
    parent_id    = str(parent["id"])
    parent_title = parent.get("name", "")
    parent_labels = parent.get("labels", [])
    repo_key     = _label_value(parent_labels, "repo")
    base_branch  = _label_value(parent_labels, "base-branch")
    retry_count  = _retry_count_from_labels(parent_labels)

    if retry_count >= _MAX_FOLLOW_UP_RETRIES:
        logger.info(
            "board_worker: refusing follow-up — parent task_id=%s already at retry-count=%d",
            parent_id, retry_count,
        )
        return ""

    description = (
        f"## Goal\n{parent_title} — {reason.replace('_', ' ')}\n\n"
        f"## Execution\n"
        f"repo: {repo_key}\n"
        f"mode: {follow_kind}\n"
        + (f"base_branch: {base_branch}\n" if base_branch else "")
    )

    # Inherit the parent's `source: ...` provenance so policy can recognise the
    # follow-up as part of an already-trusted lane (autonomy, spec-campaign).
    # Without this the policy engine review-blocks every follow-up because
    # `source: board_worker` alone isn't in the trusted set.
    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
    ]
    inherited_sources = [
        s for s in inherited_sources
        if s.lower().startswith("source:") and s.lower() != "source: board_worker"
    ]
    label_names = [
        f"task-kind: {follow_kind}",
        f"repo: {repo_key}",
        "source: board_worker",
        *inherited_sources,
        f"original-task-id: {parent_id}",
        f"handoff-reason: {reason}",
        f"retry-count: {retry_count + 1}",
    ]

    issue = client.create_issue(
        name=f"[{follow_kind}] {parent_title}",
        description=description,
        state=_STATE_READY,
        label_names=label_names,
    )
    new_id = str(issue.get("id", "?"))
    logger.info("board_worker: created follow-up task_id=%s kind=%s reason=%s", new_id, follow_kind, reason)
    return new_id


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _write_heartbeat(status_dir: Path, role: str) -> None:
    try:
        status_dir.mkdir(parents=True, exist_ok=True)
        hb = status_dir / f"heartbeat_{role}.json"
        hb.write_text(json.dumps({
            "role": role,
            "at":   datetime.now(UTC).isoformat(),
            "status": "idle",
        }), encoding="utf-8")
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_goal(description: str, title: str) -> str:
    """Pull goal text from ## Goal section, fall back to title."""
    import re
    m = re.search(r"##\s+Goal\s*\n(.*?)(?=##|\Z)", description, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()
        if text:
            return text
    return title


def _task_type_from_kind(task_kind: str) -> str:
    return {
        "goal":              "feature",
        "test":              "test",
        "test_campaign":     "test",
        "improve":           "refactor",
        "improve_campaign":  "refactor",
    }.get(task_kind, "chore")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="OperationsCenter board worker — polls Plane and executes tasks by role"
    )
    parser.add_argument("--config",                 required=True, type=Path)
    parser.add_argument("--role",                   required=True, choices=list(_ROLE_KINDS))
    parser.add_argument("--poll-interval-seconds",  type=int, default=30, dest="poll_interval")
    parser.add_argument("--status-dir",             type=Path, default=None, dest="status_dir")
    parser.add_argument("--once",                   action="store_true")
    parser.add_argument("--log-level",              default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{args.role}] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    role       = args.role
    status_dir = args.status_dir or (Path(__file__).resolve().parents[4] / "logs" / "local" / "watch-all")

    logger.info("board_worker[%s]: starting — poll_interval=%ds", role, args.poll_interval)

    while True:
        try:
            settings = _load_settings(args.config)
            client   = _plane_client(settings)
            try:
                issue = _claim_next(client, role, settings)
                if issue:
                    _process_issue(issue, role, args.config, settings, client)
                else:
                    logger.debug("board_worker[%s]: nothing ready", role)
                _write_heartbeat(status_dir, role)
            finally:
                client.close()
        except Exception as exc:
            logger.error("board_worker[%s]: unhandled error — %s", role, exc, exc_info=True)

        if args.once:
            return 0

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
