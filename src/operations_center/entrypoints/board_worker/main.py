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
import uuid
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
        for label in labels:
            name = (label.get("name", "") if isinstance(label, dict) else str(label)).strip()
            low = name.lower()
            if low.startswith("source:") or low == "review_required":
                forwarded_labels.append(name)

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
    title   = issue.get("name", "")
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
    logger.info(
        "board_worker: split task_id=%s into %d chunks (retry-count=%d → %d)",
        parent_id, len(created), retry_count, retry_count + 1,
    )
    return created


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


def _create_follow_up(client, parent: dict, settings, follow_kind: str, reason: str) -> str:
    """Create a follow-up Plane task with full lineage metadata. Returns the new task id."""
    parent_id    = str(parent["id"])
    parent_title = parent.get("name", "")
    parent_labels = parent.get("labels", [])
    repo_key     = _label_value(parent_labels, "repo")
    base_branch  = _label_value(parent_labels, "base-branch")
    parent_kind  = _label_value(parent_labels, "task-kind")

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
        f"source: board_worker",
        *inherited_sources,
        f"original-task-id: {parent_id}",
        f"handoff-reason: {reason}",
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
