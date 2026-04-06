from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
import html
import json
import logging
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from control_plane.adapters.github_pr import GitHubPRClient
from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService, TaskParser
from control_plane.config import load_settings
from control_plane.domain import ExecutionResult
from control_plane.execution.usage_store import UsageStore
TRIAGE_COMMENT_MARKER = "[Improve] Blocked triage"
UNBLOCK_COMMENT_MARKER = "[Improve] Resolution complete"
IMPROVE_COMMENT_MARKER = "[Improve] Improvement pass"
PROPOSE_COMMENT_MARKER = "[Propose] Autonomous task created"
# Set CONTROL_PLANE_NOTIFY_WEBHOOK to a URL to receive POST notifications when a
# task is blocked and requires human attention.
_NOTIFY_WEBHOOK_ENV = "CONTROL_PLANE_NOTIFY_WEBHOOK"
RATE_LIMIT_BACKOFF_MULTIPLIER = 4
UNKNOWN_BLOCKED_CLASSIFICATION = "unknown"
MAX_IMPROVE_FOLLOW_UPS_PER_CYCLE = 3
MAX_PROPOSALS_PER_CYCLE = 4
MAX_PROPOSALS_PER_DAY = 30
PROPOSAL_COOLDOWN_SECONDS = 20 * 60
PROPOSAL_WINDOW_SECONDS = 24 * 60 * 60
RECENTLY_PROPOSED_WINDOW_SECONDS = 7 * 24 * 60 * 60
LOW_BACKLOG_THRESHOLD = 6
# Don't create new proposals if this many tasks are already active (Ready for AI or Running).
# Prevents flooding the board when work is already queued.
MAX_ACTIVE_TASKS_FOR_PROPOSALS = 3
# Maximum tasks to promote from Backlog → Ready for AI when the board is idle.
MAX_BACKLOG_PROMOTIONS_PER_CYCLE = 2
# Blocked tasks with human_attention_required that have sat untouched longer than this
# will be escalated with a fresh re-triage task.
STALE_BLOCKED_ESCALATION_DAYS = 7
EXECUTION_ACTIONS = {"execute", "improve_task"}
MAX_CLASSIFICATION_ISSUES = 20
_logger = logging.getLogger(__name__)


@dataclass
class ImproveFollowUpSpec:
    task_kind: str
    title: str
    goal_text: str
    handoff_reason: str
    constraints_text: str | None = None


@dataclass
class ImproveTriageResult:
    classification: str
    certainty: str
    reason_summary: str
    recommended_action: str
    human_attention_required: bool
    follow_up: ImproveFollowUpSpec | None = None


@dataclass
class ProposalSpec:
    task_kind: str
    title: str
    goal_text: str
    reason_summary: str
    source_signal: str
    confidence: str
    recommended_state: str
    handoff_reason: str
    dedup_key: str
    constraints_text: str | None = None
    human_attention_required: bool = False
    repo_key: str = ""
    evidence_lines: list[str] = field(default_factory=list)


@dataclass
class ProposalCycleResult:
    created_task_ids: list[str]
    decision: str
    board_idle: bool
    reason_summary: str
    proposed_state: str | None = None


def worker_title(role: str) -> str:
    return role.capitalize()


def notify_human_attention(task_id: str, task_title: str, classification: str, reason: str) -> None:
    """POST a JSON notification to CONTROL_PLANE_NOTIFY_WEBHOOK if configured."""
    webhook_url = os.environ.get(_NOTIFY_WEBHOOK_ENV, "").strip()
    if not webhook_url:
        return
    payload = {
        "event": "human_attention_required",
        "task_id": task_id,
        "task_title": task_title,
        "classification": classification,
        "reason": reason,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    try:
        with httpx.Client(timeout=10) as http:
            http.post(webhook_url, json=payload)
    except Exception:
        pass  # Notification is best-effort; never block the workflow


def _record_execution_artifact(service: ExecutionService, task_id: str, result: ExecutionResult) -> None:
    """Persist a structured execution artifact in the usage store after each run.

    This feeds scope-policy learning (avoid_paths extraction), PR merge feedback
    (auto-close blocked tasks whose PR was merged), and future kodo context.
    Best-effort — never raises.
    """
    try:
        service.usage_store.record_task_artifact(
            task_id=task_id,
            artifact={
                "outcome_status": result.outcome_status,
                "changed_files": list(result.changed_files or []),
                "validation_passed": result.validation_passed,
                "blocked_classification": result.blocked_classification or "",
                "pull_request_url": result.pull_request_url or "",
                "success": result.success,
            },
            now=datetime.now(UTC),
        )
    except Exception:
        pass


def _extract_filename_tokens(title: str) -> set[str]:
    """Extract *.py filename tokens from a task title for conflict detection."""
    return {m.group(0).lower() for m in re.finditer(r"\b\w[\w.]*\.py\b", title)}


def _collect_open_pr_files(service: "ExecutionService") -> set[str]:
    """Return basenames of all files changed in open PRs across all configured repos.

    Called once per proposal cycle so the cost is amortised across all proposals.
    Requires GITHUB_TOKEN; returns an empty set on any error or missing token.

    PRs are created during execution (branch pushed while task is still Running),
    so this gives exact file-level data for first-run Running tasks that have no
    execution artifact yet.
    """
    token = service.settings.git_token()
    if not token:
        return set()
    basenames: set[str] = set()
    for repo_cfg in service.settings.repos.values():
        clone_url = getattr(repo_cfg, "clone_url", "")
        if not clone_url:
            continue
        try:
            owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(clone_url)
            gh = GitHubPRClient(token)
            for pr in gh.list_open_prs(owner, repo_name):
                pr_number = pr.get("number")
                if pr_number is None:
                    continue
                for path in gh.list_pr_files(owner, repo_name, int(pr_number)):
                    basenames.add(Path(path).name.lower())
        except Exception:
            pass
    return basenames


def _has_conflict_with_active_task(
    proposal_title: str,
    issues: list[dict[str, Any]],
    usage_store: "UsageStore | None" = None,
    open_pr_files: set[str] | None = None,
) -> bool:
    """Return True if *proposal_title* conflicts with a Running/Review task.

    Conflict resolution priority (highest fidelity first):
    1. Artifact ``changed_files`` — available for Review tasks and Running retries.
       Basenames are compared against filename tokens from the proposal title.
    2. Open PR file basenames — fetched once per cycle by ``_collect_open_pr_files``
       and passed in.  Covers first-run Running tasks whose branch was pushed but
       whose execution artifact does not yet exist.
    3. Title token matching — fallback when neither artifact nor PR data is available
       (e.g. tasks that are Ready for AI and haven't run yet).
    """
    proposal_tokens = _extract_filename_tokens(proposal_title)
    if not proposal_tokens:
        return False
    for issue in issues:
        status = issue_status_name(issue)
        if status not in ("Running", "Ready for AI", "Review"):
            continue
        task_id = str(issue["id"])

        # --- High-fidelity: artifact changed_files ---
        if usage_store is not None:
            artifact = usage_store.get_task_artifact(task_id)
            if artifact and artifact.get("changed_files"):
                artifact_basenames = {Path(f).name.lower() for f in artifact["changed_files"] if f}
                if proposal_tokens & artifact_basenames:
                    return True
                # We have authoritative data for this task — skip lower-fidelity checks.
                continue

        # --- Medium-fidelity: open PR files (Running tasks, first attempt) ---
        if open_pr_files and status == "Running":
            if proposal_tokens & open_pr_files:
                return True
            continue

        # --- Low-fidelity fallback: title token matching ---
        running_tokens = _extract_filename_tokens(str(issue.get("name", "")))
        if proposal_tokens & running_tokens:
            return True
    return False


def _check_task_pr_merged(task_id: str, usage_store: UsageStore) -> bool:
    """Return True if the PR recorded in task_id's artifact has been merged on GitHub.

    Requires GITHUB_TOKEN env var.  Returns False on any error or missing data.
    """
    artifact = usage_store.get_task_artifact(task_id)
    if not artifact:
        return False
    pr_url = str(artifact.get("pull_request_url") or "").strip()
    if not pr_url:
        return False
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not m:
        return False
    owner, repo_name, pr_number = m.group(1), m.group(2), int(m.group(3))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return False
    try:
        gh = GitHubPRClient(token)
        pr = gh.get_pr(owner, repo_name, pr_number)
        return bool(pr.get("merged")) or (
            pr.get("state") == "closed" and pr.get("merged_at") is not None
        )
    except Exception:
        return False


def render_worker_comment(title: str, bullets: list[str]) -> str:
    lines = [title]
    lines.extend(f"- {bullet}" for bullet in bullets)
    return "\n".join(lines)


def blocked_classification_token(raw: str) -> str:
    mapping = {
        "scope-policy violation": "scope_policy",
        "validation failure": "validation_failure",
        "infra/tooling": "infra_tooling",
        "parse/config issue": "parse_config",
        "context limit": "context_limit",
        "dependency missing": "dependency_missing",
        "unknown/manual attention": UNKNOWN_BLOCKED_CLASSIFICATION,
    }
    return mapping.get(raw, UNKNOWN_BLOCKED_CLASSIFICATION)


def classification_display_name(classification: str) -> str:
    return classification.replace("_", " ")


def task_kind_for_issue(issue: dict[str, Any]) -> str:
    kind = issue_task_kind(issue)
    return kind if kind else "goal"


def normalize_label_set(issue: dict[str, Any]) -> set[str]:
    return {label.strip().lower() for label in issue_label_names(issue)}


def goal_requires_test_follow_up(issue: dict[str, Any], result: ExecutionResult) -> bool:
    labels = normalize_label_set(issue)
    if "handoff:test" in labels or "needs-test" in labels:
        return True
    if "handoff:none" in labels or "no-test" in labels:
        return False
    return result.success and bool(result.changed_files)


def classify_execution_result(result: ExecutionResult) -> str:
    if result.policy_violations:
        return "scope_policy"
    excerpt = (result.execution_stderr_excerpt or "").lower()
    # Context-window exhaustion — kodo ran out of tokens mid-task (check before validation_failure)
    if any(
        token in excerpt
        for token in [
            "context window",
            "context length",
            "maximum context",
            "context_length_exceeded",
            "token limit",
            "exceeds the maximum",
            "too long for",
        ]
    ):
        return "context_limit"
    # Missing Python/Node dependency (check before validation_failure)
    if any(
        token in excerpt
        for token in [
            "modulenotfounderror",
            "importerror",
            "no module named",
            "cannot import name",
            "command not found",
            "npm err",
        ]
    ):
        return "dependency_missing"
    if not result.validation_passed:
        return "validation_failure"
    # Infrastructure / auth / tooling failures
    if any(
        token in excerpt
        for token in [
            "api key not set",
            "authentication",
            "auth",
            "login required",
            "no such file or directory",
            "timed out",
            "timeout",
        ]
    ):
        return "infra_tooling"
    return UNKNOWN_BLOCKED_CLASSIFICATION


def parse_context_value(description: str, key: str) -> str | None:
    match = re.search(rf"^- {re.escape(key)}:\s*(.+?)\s*$", description, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def parse_execution_value(description: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", description, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def status_file_path(status_dir: Path | None, role: str) -> Path | None:
    if status_dir is None:
        return None
    status_dir.mkdir(parents=True, exist_ok=True)
    return status_dir / f"{role}.status.json"


def proposal_memory_path(status_dir: Path | None) -> Path | None:
    if status_dir is None:
        return None
    status_dir.mkdir(parents=True, exist_ok=True)
    return status_dir / "propose.memory.json"


def load_proposal_memory(status_dir: Path | None) -> dict[str, Any]:
    path = proposal_memory_path(status_dir)
    if path is None or not path.exists():
        return {"last_proposal_at": None, "proposal_timestamps": [], "proposed_index": {}}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"last_proposal_at": None, "proposal_timestamps": [], "proposed_index": {}}
    return {
        "last_proposal_at": payload.get("last_proposal_at"),
        "proposal_timestamps": list(payload.get("proposal_timestamps", [])),
        "proposed_index": dict(payload.get("proposed_index", {})),
    }


def save_proposal_memory(status_dir: Path | None, memory: dict[str, Any]) -> None:
    path = proposal_memory_path(status_dir)
    if path is None:
        return
    path.write_text(json.dumps(memory, indent=2))


def write_watch_status(
    *,
    status_dir: Path | None,
    role: str,
    cycle: int,
    state: str,
    run_id: str,
    last_action: str,
    task_id: str | None = None,
    task_kind: str | None = None,
    follow_up_task_ids: list[str] | None = None,
    blocked_classification: str | None = None,
    counters: dict[str, int] | None = None,
) -> None:
    path = status_file_path(status_dir, role)
    if path is None:
        return
    payload = {
        "role": role,
        "cycle": cycle,
        "state": state,
        "run_id": run_id,
        "last_action": last_action,
        "task_id": task_id,
        "task_kind": task_kind,
        "follow_up_task_ids": follow_up_task_ids or [],
        "blocked_classification": blocked_classification,
        "updated_at": datetime.now(UTC).isoformat(),
        "counters": counters or {},
    }
    path.write_text(json.dumps(payload, indent=2))


def execution_gate_decision(
    *,
    service: ExecutionService,
    role: str,
    action: str,
    issue: dict[str, Any],
    now: datetime | None = None,
) -> tuple[str, dict[str, object]] | None:
    if action not in EXECUTION_ACTIONS:
        return None
    now = now or datetime.now(UTC)
    store = service.usage_store
    task_id = str(issue.get("id"))
    signature = store.issue_signature(issue)
    noop = store.noop_decision(role=role, task_id=task_id, signature=signature)
    if noop.should_skip:
        store.record_skip(
            role=role,
            task_id=task_id,
            signature=signature,
            reason=noop.reason or "no_op",
            detail=noop.detail,
            now=now,
        )
        return "skip_noop", {"reason": noop.reason, "detail": noop.detail}

    if role in {"goal", "test"}:
        retry = store.retry_decision(task_id=task_id)
        if not retry.allowed:
            store.record_retry_cap(role=role, task_id=task_id, now=now, attempts=retry.attempts, limit=retry.limit)
            return "retry_cap_block", {"reason": retry.reason, "attempts": retry.attempts, "limit": retry.limit}

    budget = store.budget_decision(now=now)
    if not budget.allowed:
        store.record_skip(
            role=role,
            task_id=task_id,
            signature=signature,
            reason=budget.reason or "budget_exceeded",
            detail=budget.window,
            now=now,
            evidence={"limit": budget.limit, "current": budget.current},
        )
        return "skip_budget", {
            "reason": budget.reason,
            "window": budget.window,
            "limit": budget.limit,
            "current": budget.current,
        }

    store.record_execution(role=role, task_id=task_id, signature=signature, now=now)
    return None


def latest_run_dir(result: ExecutionResult) -> Path | None:
    for artifact in result.artifacts:
        artifact_path = Path(artifact)
        if artifact_path.name == "result_summary.md":
            return artifact_path.parent
    return None


def run_service_task(service: ExecutionService, client: PlaneClient, task_id: str, *, worker_role: str) -> ExecutionResult:
    try:
        return service.run_task(client, task_id, worker_role=worker_role, preauthorized=True)
    except TypeError as exc:
        if "worker_role" not in str(exc) and "preauthorized" not in str(exc):
            raise
        return service.run_task(client, task_id)  # type: ignore[call-arg]


def rewrite_worker_summary(
    result: ExecutionResult,
    service: ExecutionService,
    task_id: str | None = None,
) -> None:
    if task_id is not None:
        _record_execution_artifact(service, task_id, result)
    run_dir = latest_run_dir(result)
    if run_dir is None:
        return
    service.reporter.write_summary(run_dir, result)


def issue_status_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", ""))
    return str(state or "")


def issue_label_names(issue: dict[str, Any]) -> list[str]:
    raw_labels = issue.get("labels", [])
    labels: list[str] = []
    if isinstance(raw_labels, list):
        for raw in raw_labels:
            if isinstance(raw, dict):
                name = raw.get("name")
                if name:
                    labels.append(str(name))
            elif raw:
                labels.append(str(raw))
    return labels


def issue_task_kind(issue: dict[str, Any]) -> str:
    for label in issue_label_names(issue):
        normalized = label.strip().lower()
        if normalized.startswith("task-kind:"):
            return normalized.split(":", 1)[1].strip()
    return "goal"


def issue_needs_detail(issue: dict[str, Any]) -> bool:
    raw_labels = issue.get("labels")
    return not isinstance(raw_labels, list) or len(raw_labels) == 0


def issue_source(issue: dict[str, Any]) -> str | None:
    for label in issue_label_names(issue):
        normalized = label.strip().lower()
        if normalized.startswith("source:"):
            return normalized.split(":", 1)[1].strip()
    return None


_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def issue_priority(issue: dict[str, Any]) -> int:
    """Return a sort key for task priority (0=high, 1=medium, 2=low, 3=unset)."""
    for label in issue_label_names(issue):
        normalized = label.strip().lower()
        if normalized.startswith("priority:"):
            val = normalized.split(":", 1)[1].strip()
            return _PRIORITY_ORDER.get(val, 3)
    return 3


def issue_is_improve_generated(issue: dict[str, Any]) -> bool:
    return issue_source(issue) == "improve-worker"


def issue_is_unblock_chain(issue: dict[str, Any]) -> bool:
    title = str(issue.get("name", "")).strip().lower()
    return issue_is_improve_generated(issue) or title.startswith("unblock ")


def is_open_issue(issue: dict[str, Any]) -> bool:
    return issue_status_name(issue).strip().lower() not in {"done", "cancelled"}


def open_goal_or_test_count(client: PlaneClient) -> int:
    count = 0
    for issue in client.list_issues():
        if not is_open_issue(issue):
            continue
        if issue_task_kind(issue) in {"goal", "test"}:
            count += 1
    return count


def ready_or_running_goal_or_test_count(client: PlaneClient) -> int:
    count = 0
    for issue in client.list_issues():
        if not is_open_issue(issue):
            continue
        if issue_task_kind(issue) not in {"goal", "test"}:
            continue
        if issue_status_name(issue) in {"Ready for AI", "Running"}:
            count += 1
    return count


def board_is_idle_for_proposals(client: PlaneClient) -> bool:
    return ready_or_running_goal_or_test_count(client) == 0 and open_goal_or_test_count(client) <= LOW_BACKLOG_THRESHOLD


def board_is_idle_for_proposals_from_issues(issues: list[dict[str, Any]], *, repo_key: str | None = None) -> bool:
    """Return True when the board has capacity for new proposals.

    When repo_key is given, only tasks belonging to that repo count toward the
    active-work gate — so a busy ControlPlane board no longer blocks
    code_youtube_shorts proposals and vice versa.
    """
    open_count = 0
    ready_or_running_count = 0
    for issue in issues:
        if not is_open_issue(issue):
            continue
        if issue_task_kind(issue) not in {"goal", "test"}:
            continue
        if repo_key is not None:
            # Only count tasks whose repo label matches this repo.
            labels = issue_label_names(issue)
            issue_repo = next(
                (lbl.split(":", 1)[1].strip() for lbl in labels if lbl.lower().startswith("repo:")),
                None,
            )
            if issue_repo is not None and issue_repo.lower() != repo_key.lower():
                continue
        open_count += 1
        if issue_status_name(issue) in {"Ready for AI", "Running"}:
            ready_or_running_count += 1
    return ready_or_running_count == 0 and open_count <= LOW_BACKLOG_THRESHOLD


def active_task_count_from_issues(issues: list[dict[str, Any]]) -> int:
    """Count goal/test tasks currently in Ready for AI or Running state."""
    count = 0
    for issue in issues:
        if issue_task_kind(issue) not in {"goal", "test"}:
            continue
        if issue_status_name(issue) in {"Ready for AI", "Running"}:
            count += 1
    return count


def promote_backlog_tasks(
    client: PlaneClient,
    issues: list[dict[str, Any]],
    *,
    max_promotions: int = MAX_BACKLOG_PROMOTIONS_PER_CYCLE,
) -> list[str]:
    """Promote the oldest Backlog tasks to Ready for AI when the board is idle.

    Only promotes tasks that were created by the autonomy system (have a
    PROPOSE_COMMENT_MARKER comment or a 'source: proposer' / 'source: autonomy'
    label), to avoid promoting manually-created Backlog items unexpectedly.
    Returns list of promoted task IDs.
    """
    candidates: list[dict[str, Any]] = []
    for issue in issues:
        if issue_status_name(issue).strip().lower() != "backlog":
            continue
        if issue_task_kind(issue) not in {"goal", "test"}:
            continue
        labels = issue_label_names(issue)
        source = next(
            (lbl.split(":", 1)[1].strip().lower() for lbl in labels if lbl.lower().startswith("source:")),
            "",
        )
        if source in {"proposer", "autonomy", "improve-worker"}:
            candidates.append(issue)
    if not candidates:
        return []
    promoted: list[str] = []
    for issue in candidates[:max_promotions]:
        task_id = str(issue["id"])
        try:
            client.transition_issue(task_id, "Ready for AI")
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Propose] Backlog task promoted to Ready for AI",
                    [
                        f"task_id: {task_id}",
                        "reason: board was idle with no active tasks",
                        "action: promoted from Backlog to Ready for AI",
                    ],
                ),
            )
            promoted.append(task_id)
        except Exception:
            pass
    return promoted


_PR_REVIEW_STATE_DIR = Path("state/pr_reviews")


def _has_active_pr_review(task_id: str) -> bool:
    """Return True if a PR review state file exists for this task — reviewer watcher owns it."""
    return (_PR_REVIEW_STATE_DIR / f"{task_id}.json").exists()


def select_ready_task_id(client: PlaneClient, ready_state: str = "Ready for AI", role: str = "goal") -> str:
    issues = client.list_issues()
    for issue in issues:
        task_id = str(issue["id"])
        status_name = issue_status_name(issue)
        task_kind = issue_task_kind(issue)
        if issue_needs_detail(issue):
            detailed_issue = client.fetch_issue(task_id)
            status_name = issue_status_name(detailed_issue)
            task_kind = issue_task_kind(detailed_issue)
        if task_kind != role:
            continue
        if _has_active_pr_review(task_id):
            continue
        if status_name == ready_state:
            return task_id
        if not status_name or status_name == str(issue.get("state", "")):
            detailed_issue = client.fetch_issue(task_id)
            if issue_task_kind(detailed_issue) == role and issue_status_name(detailed_issue) == ready_state:
                return task_id
    raise ValueError(f"No work item found in state '{ready_state}'")


# ---------------------------------------------------------------------------
# Point 1: Goal coherence — focus_areas proposal scoring
# ---------------------------------------------------------------------------

def _proposal_matches_focus_areas(proposal: "ProposalSpec", focus_areas: list[str]) -> bool:
    """Return True if any focus_area keyword appears in the proposal title or goal text."""
    if not focus_areas:
        return True  # No filter configured — all proposals pass
    text = f"{proposal.title} {proposal.goal_text}".lower()
    return any(area.lower() in text for area in focus_areas)


# ---------------------------------------------------------------------------
# Point 2: Task dependency ordering
# ---------------------------------------------------------------------------

_DEPENDS_ON_PATTERN = re.compile(r"^\s*(?:-\s*)?depends_on:\s*(.+)", re.MULTILINE)
_UUID_LIKE = re.compile(r"^[0-9a-f][-0-9a-f]{7,}$", re.I)


def parse_task_dependencies(description: str) -> list[str]:
    """Return task IDs listed under ``depends_on:`` in *description*.

    Supports both plain and bullet-list format::

        depends_on: uuid-1, uuid-2
        - depends_on: uuid-1
    """
    match = _DEPENDS_ON_PATTERN.search(description)
    if not match:
        return []
    raw = match.group(1).strip()
    parts = [p.strip() for p in re.split(r"[,\s]+", raw) if p.strip()]
    return [p for p in parts if _UUID_LIKE.match(p)]


def task_dependencies_met(client: PlaneClient, task_id: str) -> bool:
    """Return True if all ``depends_on`` task IDs for *task_id* are Done or Cancelled."""
    try:
        issue = client.fetch_issue(task_id)
        description = issue_description_text(issue)
        dep_ids = parse_task_dependencies(description)
        if not dep_ids:
            return True
        for dep_id in dep_ids:
            try:
                dep = client.fetch_issue(dep_id)
                if issue_status_name(dep) not in ("Done", "Cancelled"):
                    return False
            except Exception:
                return False  # Can't fetch dep → treat as unmet
    except Exception:
        return True  # Defensive: don't block tasks we can't inspect
    return True


# ---------------------------------------------------------------------------
# Point 3: Task sizing gate — split oversized decompose findings
# ---------------------------------------------------------------------------

# Proposals for files with more than this many lines get split into bounded parts.
MAX_TASK_LINES_FOR_DIRECT_EXECUTION = 800
_MAX_FNS_PER_PART = 5
_DECOMPOSE_TITLE_RE = re.compile(
    r"^Decompose (\S+\.py) \((\d+)[lL],\s*(\d+) oversized function",
    re.IGNORECASE,
)


def _split_oversized_finding(finding: dict[str, str]) -> list[dict[str, str]]:
    """Split a 'Decompose X.py (NL, M oversized functions)' finding into bounded parts.

    Returns the original finding unchanged if it doesn't match the pattern or is
    already small enough.  Otherwise returns 2–4 part-specific findings so kodo
    has a tractable scope per execution.
    """
    title = finding.get("title", "")
    m = _DECOMPOSE_TITLE_RE.match(title)
    if not m:
        return [finding]
    filename, line_count, fn_count = m.group(1), int(m.group(2)), int(m.group(3))
    if line_count <= MAX_TASK_LINES_FOR_DIRECT_EXECUTION and fn_count <= _MAX_FNS_PER_PART:
        return [finding]
    n_parts = min(4, max(2, (fn_count + _MAX_FNS_PER_PART - 1) // _MAX_FNS_PER_PART))
    fns_per_part = max(1, fn_count // n_parts)
    result = []
    for i in range(n_parts):
        part = i + 1
        result.append({
            **finding,
            "title": f"Decompose {filename} — part {part} of {n_parts}",
            "goal": (
                f"{finding.get('goal', '')} "
                f"This is part {part} of {n_parts}: extract approximately {fns_per_part} of the "
                f"oversized functions into well-named helpers or sub-modules. "
                f"Leave the remaining functions for subsequent parts."
            ),
            "constraints": (
                f"{finding.get('constraints', '')}\n"
                f"- decomposition_part: {part} of {n_parts}\n"
                f"- target_functions_this_part: ~{fns_per_part}\n"
                f"- do not touch functions not in scope for this part\n"
                f"- ensure all existing tests still pass after each extraction"
            ),
        })
    return result


# ---------------------------------------------------------------------------
# Points 4 + 6: Post-merge CI feedback and regression task creation
# ---------------------------------------------------------------------------

_REGRESSION_MARKER = "[Improve] Post-merge regression detected"
_MAX_POST_MERGE_CHECKS_PER_CYCLE = 5


def _regression_already_created(client: PlaneClient, task_id: str) -> bool:
    for comment in client.list_comments(task_id):
        if _REGRESSION_MARKER.lower() in extract_comment_text(comment).lower():
            return True
    return False


def _repo_key_from_pr_url(pr_url: str, service: "ExecutionService") -> str:
    for repo_key, repo_cfg in service.settings.repos.items():
        clone_url = getattr(repo_cfg, "clone_url", "")
        try:
            owner, repo = GitHubPRClient.owner_repo_from_clone_url(clone_url)
            if f"github.com/{owner}/{repo}" in pr_url:
                return repo_key
        except Exception:
            pass
    return default_repo_key(service)


def detect_post_merge_regressions(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    issues: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Scan Done tasks for post-merge CI failures and create regression tasks.

    For each Done task whose execution artifact holds a ``pull_request_url``
    pointing to a merged PR, checks if CI passed on the merge commit.  If CI
    failed and no regression task exists yet, creates a high-priority goal task
    and leaves a marker comment so the check isn't repeated.

    Returns a list of newly-created regression task IDs.
    """
    token = service.settings.git_token()
    if not token:
        return []
    issues = issues or client.list_issues()
    store = service.usage_store
    created_ids: list[str] = []
    checked = 0
    for issue in issues:
        if checked >= _MAX_POST_MERGE_CHECKS_PER_CYCLE:
            break
        if issue_status_name(issue) != "Done":
            continue
        task_id = str(issue["id"])
        artifact = store.get_task_artifact(task_id)
        if not artifact:
            continue
        pr_url = str(artifact.get("pull_request_url") or "").strip()
        if not pr_url:
            continue
        mpr = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
        if not mpr:
            continue
        if _regression_already_created(client, task_id):
            continue
        owner, repo_name, pr_number = mpr.group(1), mpr.group(2), int(mpr.group(3))
        checked += 1
        try:
            gh = GitHubPRClient(token)
            pr = gh.get_pr(owner, repo_name, pr_number)
            if not (pr.get("merged") or pr.get("merged_at")):
                continue
            merged_sha = (pr.get("head") or {}).get("sha", "")
            failed_checks = gh.get_failed_checks(owner, repo_name, pr_number, pr_data=pr)
            if not failed_checks:
                continue  # CI clean — nothing to do
            task_title = str(issue.get("name", "unknown task"))
            repo_key = _repo_key_from_pr_url(pr_url, service)
            regression_task = client.create_issue(
                name=f"Regression from: {task_title}",
                description=(
                    f"## Execution\nrepo: {repo_key}\nmode: goal\n\n"
                    "## Goal\n"
                    f"CI failed after merging the PR from task '{task_title}'. "
                    "Investigate the failing checks and either fix the regression or "
                    "revert the change if the fix is not straightforward.\n\n"
                    "## Context\n"
                    f"- source_task_id: {task_id}\n"
                    f"- pull_request_url: {pr_url}\n"
                    + (f"- merged_sha: {merged_sha}\n" if merged_sha else "")
                    + f"- failed_checks: {'; '.join(failed_checks[:3])}\n"
                    "- recommended_action: fix_or_revert\n"
                    "- priority: high\n"
                ),
                state="Ready for AI",
                label_names=["task-kind: goal", "priority: high", "source: post-merge-ci"],
            )
            reg_id = str(regression_task.get("id", ""))
            created_ids.append(reg_id)
            client.comment_issue(
                task_id,
                render_worker_comment(
                    _REGRESSION_MARKER,
                    [
                        f"regression_task_id: {reg_id}",
                        f"failed_checks: {'; '.join(failed_checks[:3])}",
                        f"pull_request_url: {pr_url}",
                    ],
                ),
            )
        except Exception:
            pass
    return created_ids


# ---------------------------------------------------------------------------
# Point 5: Self-modification controls
# ---------------------------------------------------------------------------

def _is_self_repo(repo_key: str, service: "ExecutionService") -> bool:
    """Return True if *repo_key* identifies the ControlPlane installation itself."""
    self_key = getattr(service.settings, "self_repo_key", None)
    if self_key:
        return repo_key.lower() == self_key.lower()
    # Fallback: any repo whose local_path equals cwd is "self"
    cwd = Path.cwd().resolve()
    repo_cfg = service.settings.repos.get(repo_key)
    if repo_cfg:
        local = getattr(repo_cfg, "local_path", None)
        if local and Path(local).resolve() == cwd:
            return True
    return False


def _self_modify_approved(issue: dict[str, Any]) -> bool:
    """Return True if the task carries a 'self-modify: approved' label."""
    return any("self-modify: approved" in lbl.lower() for lbl in issue_label_names(issue))


def select_watch_candidate(
    client: PlaneClient,
    *,
    ready_state: str,
    role: str,
    known_triaged_blocked_ids: set[str] | None = None,
    skip_ids: set[str] | None = None,
    service: "ExecutionService | None" = None,
) -> tuple[str, str]:
    issues = client.list_issues()
    if role == "improve":
        # First priority: fix_pr tasks (CI-fix tasks created by the CI monitor).
        for issue in issues:
            task_id = str(issue["id"])
            candidate = client.fetch_issue(task_id) if issue_needs_detail(issue) else issue
            if issue_status_name(candidate) == ready_state and issue_task_kind(candidate) == "fix_pr":
                return task_id, "fix_pr_task"
        # Second priority: improve tasks.
        for issue in issues:
            task_id = str(issue["id"])
            candidate = client.fetch_issue(task_id) if issue_needs_detail(issue) else issue
            if issue_status_name(candidate) == ready_state and issue_task_kind(candidate) == "improve":
                return task_id, "improve_task"
        for issue in issues:
            task_id = str(issue["id"])
            candidate = client.fetch_issue(task_id) if issue_needs_detail(issue) else issue
            if issue_status_name(candidate) != "Blocked":
                continue
            if known_triaged_blocked_ids is not None and task_id in known_triaged_blocked_ids:
                continue
            if issue_is_unblock_chain(candidate):
                if known_triaged_blocked_ids is not None:
                    known_triaged_blocked_ids.add(task_id)
                continue
            if not blocked_issue_already_triaged(client, task_id):
                # Check if the task's PR was already merged before triaging — if so,
                # the implementation succeeded and we can auto-close without triage.
                if _check_task_pr_merged(task_id, UsageStore()):
                    return task_id, "blocked_pr_merged"
                return task_id, "blocked_triage"
            if blocked_resolution_is_complete(client, task_id):
                return task_id, "blocked_resolution_complete"
            # Stale dead-end: human_attention with no follow-ups, sitting for >7 days.
            # Create a fresh re-triage task rather than leaving it frozen forever.
            if (
                blocked_issue_is_stale(candidate)
                and get_triage_human_attention_flag(client, task_id)
                and not extract_triage_follow_up_ids(client, task_id)
                and not blocked_issue_already_escalated(client, task_id)
            ):
                return task_id, "blocked_stale_escalation"
            if known_triaged_blocked_ids is not None:
                known_triaged_blocked_ids.add(task_id)
        raise ValueError("No improve work item found for blocked triage or improve task routing")

    # Sort by priority label (high → medium → low → unset) before iterating.
    sorted_issues = sorted(issues, key=issue_priority)
    for issue in sorted_issues:
        task_id = str(issue["id"])
        if skip_ids and task_id in skip_ids:
            continue
        status_name = issue_status_name(issue)
        task_kind = issue_task_kind(issue)
        if issue_needs_detail(issue):
            detailed_issue = client.fetch_issue(task_id)
            status_name = issue_status_name(detailed_issue)
            task_kind = issue_task_kind(detailed_issue)
        if task_kind != role:
            continue
        if _has_active_pr_review(task_id):
            continue
        if status_name == ready_state:
            # Point 5: Self-modification guard — require explicit approval label
            if service is not None:
                iss = detailed_issue if issue_needs_detail(issue) else issue
                repo_key = _extract_repo_key(iss, service)
                if _is_self_repo(repo_key, service) and not _self_modify_approved(iss):
                    continue  # Skip until "self-modify: approved" label is added
            # Point 2: Skip tasks with unmet dependencies
            if not task_dependencies_met(client, task_id):
                continue
            return task_id, "execute"
    raise ValueError(f"No work item found in state '{ready_state}'")


def extract_comment_text(comment: dict[str, Any]) -> str:
    raw = comment.get("comment_html") or comment.get("comment_stripped") or comment.get("comment")
    if not isinstance(raw, str):
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(p|div|ul|ol|li)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def issue_description_text(issue: dict[str, Any]) -> str:
    raw = issue.get("description") or issue.get("description_stripped")
    if isinstance(raw, str) and raw.strip():
        return raw
    html_body = issue.get("description_html")
    if isinstance(html_body, str) and html_body.strip():
        text = html.unescape(html_body)
        text = re.sub(r"<h[1-6][^>]*>\s*(.*?)\s*</h[1-6]>", lambda m: f"\n## {m.group(1)}\n", text, flags=re.I | re.S)
        text = re.sub(r"<li[^>]*>\s*(.*?)\s*</li>", lambda m: f"- {m.group(1)}\n", text, flags=re.I | re.S)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"</?(p|div|ul|ol|pre)[^>]*>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    return ""


STALE_ESCALATION_MARKER = "[Improve] Stale blocked escalation"


def blocked_issue_already_triaged(client: PlaneClient, task_id: str) -> bool:
    for comment in client.list_comments(task_id):
        if TRIAGE_COMMENT_MARKER.lower() in extract_comment_text(comment).lower():
            return True
    return False


def blocked_issue_already_escalated(client: PlaneClient, task_id: str) -> bool:
    """Return True if this task was already escalated (stale escalation marker present)."""
    for comment in client.list_comments(task_id):
        if STALE_ESCALATION_MARKER.lower() in extract_comment_text(comment).lower():
            return True
    return False


def blocked_issue_is_stale(issue: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Return True if this blocked task has been sitting untouched past STALE_BLOCKED_ESCALATION_DAYS."""
    now = now or datetime.now(UTC)
    raw = issue.get("updated_at") or issue.get("created_at")
    if not raw:
        return False
    try:
        updated = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return (now - updated).days >= STALE_BLOCKED_ESCALATION_DAYS
    except (ValueError, TypeError):
        return False


def get_triage_human_attention_flag(client: PlaneClient, task_id: str) -> bool:
    """Return True if the triage comment on this task set human_attention_required: true."""
    for comment in client.list_comments(task_id):
        text = extract_comment_text(comment)
        if TRIAGE_COMMENT_MARKER.lower() not in text.lower():
            continue
        val = parse_context_value(text, "human_attention_required") or parse_execution_value(text, "human_attention_required")
        return (val or "").strip().lower() == "true"
    return False


def extract_triage_follow_up_ids(client: PlaneClient, task_id: str) -> list[str]:
    """Return the follow_up_task_ids recorded in the improve triage comment, if any.

    Comments are stored as Markdown but returned from the API as rendered HTML.
    After stripping HTML tags the bullet dashes are gone, so we match either
    '- follow_up_task_ids: ...' (raw markdown) or 'follow_up_task_ids: ...' (stripped HTML).
    """
    for comment in client.list_comments(task_id):
        text = extract_comment_text(comment)
        if TRIAGE_COMMENT_MARKER.lower() not in text.lower():
            continue
        # Try both with and without leading dash (markdown vs HTML-stripped)
        raw = parse_context_value(text, "follow_up_task_ids") or parse_execution_value(text, "follow_up_task_ids")
        if not raw or raw.strip().lower() == "none":
            return []
        return [part.strip() for part in raw.split(",") if part.strip() and part.strip().lower() != "none"]
    return []


def blocked_resolution_is_complete(client: PlaneClient, task_id: str) -> bool:
    """Return True if all follow-up resolution tasks are Done/Cancelled and this task
    hasn't already been unblocked by a previous resolution pass."""
    already_unblocked = any(
        UNBLOCK_COMMENT_MARKER.lower() in extract_comment_text(c).lower()
        for c in client.list_comments(task_id)
    )
    if already_unblocked:
        return False
    follow_up_ids = extract_triage_follow_up_ids(client, task_id)
    if not follow_up_ids:
        return False
    terminal = {"done", "cancelled"}
    for fid in follow_up_ids:
        try:
            issue = client.fetch_issue(fid)
        except Exception:
            return False
        if issue_status_name(issue).lower() not in terminal:
            return False
    return True


def parse_section_lines(text: str, heading: str) -> list[str]:
    pattern = rf"^##\s+{re.escape(heading)}\s*$"
    lines = text.splitlines()
    collecting = False
    values: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if re.match(pattern, line.strip(), flags=re.I):
            collecting = True
            continue
        if collecting and line.strip().startswith("## "):
            break
        if collecting and line.strip().startswith("- "):
            values.append(line.strip()[2:].strip())
    return values


def issue_evidence_lines(issue: dict[str, Any]) -> list[str]:
    return parse_section_lines(issue_description_text(issue), "Evidence")


def selected_evidence_from_issue(issue: dict[str, Any]) -> str:
    evidence = issue_evidence_lines(issue)
    return evidence[0] if evidence else "none"


def target_area_hint_from_issue(issue: dict[str, Any]) -> str:
    for line in issue_evidence_lines(issue):
        lowered = line.lower()
        if lowered.startswith("recently changed files:"):
            return line.split(":", 1)[1].strip() or "none"
    return "none"


def existing_follow_up_keys(client: PlaneClient, *, issues: list[dict[str, Any]] | None = None) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for issue in issues or client.list_issues():
        state_name = issue_status_name(issue).strip().lower()
        if state_name in {"done", "cancelled"}:
            continue
        description = issue_description_text(issue)
        source_task_id = parse_context_value(description, "original_task_id")
        handoff_reason = parse_context_value(description, "handoff_reason")
        follow_up_task_kind = parse_context_value(description, "follow_up_task_kind") or task_kind_for_issue(issue)
        if source_task_id and handoff_reason and follow_up_task_kind:
            keys.add((source_task_id, follow_up_task_kind.strip().lower(), handoff_reason.strip().lower()))
    return keys


def existing_proposal_keys(client: PlaneClient, *, issues: list[dict[str, Any]] | None = None) -> set[str]:
    keys: set[str] = set()
    for issue in issues or client.list_issues():
        if not is_open_issue(issue):
            continue
        description = issue_description_text(issue)
        proposal_key = parse_context_value(description, "proposal_dedup_key")
        if proposal_key:
            keys.add(proposal_key.strip().lower())
    return keys


def _summarise_prior_failures(client: PlaneClient, task_id: str) -> str:
    """Return a compact summary of prior execution failures from task comments."""
    classifications: list[str] = []
    outcomes: list[str] = []
    for comment in client.list_comments(task_id):
        text = extract_comment_text(comment)
        m = re.search(r"blocked_classification:\s*([a-z_]+)", text)
        if m:
            classifications.append(m.group(1))
        m = re.search(r"outcome_reason:\s*(\S+)", text)
        if m and m.group(1) not in {"none", ""}:
            outcomes.append(m.group(1))
    parts: list[str] = []
    if classifications:
        parts.append("classifications=" + ",".join(dict.fromkeys(classifications)))
    if outcomes:
        parts.append("outcomes=" + ",".join(dict.fromkeys(outcomes)))
    return "; ".join(parts)


def recent_classification_counts(client: PlaneClient, *, issues: list[dict[str, Any]] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    scanned = 0
    for issue in issues or client.list_issues():
        if scanned >= MAX_CLASSIFICATION_ISSUES:
            break
        if issue_status_name(issue) not in {"Blocked", "Done", "Cancelled", "Review", "Running"}:
            continue
        scanned += 1
        comments = client.list_comments(str(issue["id"]))
        for comment in comments:
            text = extract_comment_text(comment)
            match = re.search(r"blocked_classification:\s*([a-z_]+)", text)
            if not match:
                continue
            classification = match.group(1).strip().lower()
            counts[classification] = counts.get(classification, 0) + 1
    return counts


def reconcile_stale_running_issues(
    client: PlaneClient,
    *,
    role: str,
    ready_state: str,
    usage_store: "UsageStore | None" = None,
) -> list[str]:
    if role not in {"goal", "test", "improve"}:
        return []
    store = usage_store or UsageStore()
    usage_data = store.load()
    task_attempts: dict[str, int] = {
        k: int(v) for k, v in usage_data.get("task_attempts", {}).items()
    }
    task_signatures: dict[str, str | None] = usage_data.get("last_task_signatures", {})
    reconciled: list[str] = []
    for issue in client.list_issues():
        if issue_status_name(issue) != "Running":
            continue
        task_kind = issue_task_kind(issue)
        if role == "improve":
            if task_kind not in {"improve", "fix_pr"}:
                continue
        elif task_kind != role:
            continue
        task_id = str(issue["id"])
        attempts = task_attempts.get(task_id, 0)
        has_signature = bool(task_signatures.get(task_id))
        # Re-queue when the interruption was clearly operational (not a real failure):
        #   - attempts == 0: watcher restarted right after claiming, kodo never ran
        #   - attempts == 1 and no signature: kodo started but was interrupted before
        #     writing a completion signature — treat as a clean retry
        # Block (human review) when kodo actually completed at least one round:
        #   - attempts >= 2: retried and failed more than once
        #   - attempts >= 1 and has_signature: executed to completion but left Running
        should_requeue = attempts == 0 or (attempts == 1 and not has_signature)
        # Collect prior failure context from existing comments to aid future runs.
        prior_context = _summarise_prior_failures(client, task_id)
        if should_requeue:
            client.transition_issue(task_id, ready_state)
            bullets = [
                f"task_id: {task_id}",
                f"task_kind: {task_kind}",
                f"result_status: {ready_state.lower().replace(' ', '_')}",
                f"attempts: {attempts}",
                "reason: execution was interrupted before completion — safe to retry",
            ]
            if prior_context:
                bullets.append(f"prior_failure_context: {prior_context}")
            client.comment_issue(task_id, render_worker_comment(
                f"[{worker_title(role)}] Re-queued after interrupted execution", bullets,
            ))
        else:
            client.transition_issue(task_id, "Blocked")
            bullets = [
                f"task_id: {task_id}",
                f"task_kind: {task_kind}",
                "result_status: blocked",
                f"attempts: {attempts}",
                "reason: task ran to completion or retried multiple times without success",
            ]
            if prior_context:
                bullets.append(f"prior_failure_context: {prior_context}")
            client.comment_issue(task_id, render_worker_comment(
                f"[{worker_title(role)}] Stale running task requires human review", bullets,
            ))
        reconciled.append(task_id)
    return reconciled


def classify_blocked_issue(issue: dict[str, Any], comments: list[dict[str, Any]]) -> tuple[str, str]:
    chunks = [str(issue.get("name", "")), str(issue.get("description", "")), str(issue.get("description_html", ""))]
    chunks.extend(extract_comment_text(comment) for comment in comments)
    lowered = "\n".join(chunk for chunk in chunks if chunk).lower()

    if "policy_violations:" in lowered or "policy=failed" in lowered:
        return "scope_policy", "Changes landed outside the allowed repo scope."
    if task_kind_for_issue(issue) == "test" and ("validation_passed: false" in lowered or "validation=failed" in lowered):
        return "verification_failure", "Verification failed and needs implementation follow-up."
    if "validation_passed: false" in lowered or "validation=failed" in lowered:
        return "validation_failure", "Validation failed after execution."
    if any(
        token in lowered
        for token in [
            "context window", "context length", "maximum context",
            "context_length_exceeded", "token limit", "exceeds the maximum",
            "blocked_classification: context_limit",
        ]
    ):
        return "context_limit", "Kodo exhausted its context window before completing the task — break it into a smaller scope."
    if any(
        token in lowered
        for token in [
            "modulenotfounderror", "importerror", "no module named",
            "cannot import name", "command not found",
            "blocked_classification: dependency_missing",
        ]
    ):
        return "dependency_missing", "A required dependency or tool was not installed in the execution environment."
    if any(
        token in lowered
        for token in [
            "anthropic_api_key not set",
            "api key not set",
            "login required",
            "authentication",
            "auth",
            "execution_stderr: error:",
            "no such file or directory",
            "phase: bootstrap",
            "phase: kodo",
            "phase: repo_setup",
            "tooling",
            "installation failed",
        ]
    ):
        return "infra_tooling", "The run failed because tooling or the local execution environment was unavailable."
    if any(
        token in lowered
        for token in [
            "missing '## execution'",
            "missing '## goal'",
            "unsupported execution mode",
            "base branch",
            "phase: fetch_task",
            "taskcontracterror",
            "contract validation",
            "config error",
            "parse error",
            "missing execution metadata",
            "no clone_url configured",
            "unknown repo key",
            "no repo key",
        ]
    ):
        return "parse_config", "The work item contract or repo configuration is invalid for execution."
    return UNKNOWN_BLOCKED_CLASSIFICATION, "The failure needs human review or a more specific follow-up task."


def build_improve_triage_result(
    client: PlaneClient,
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    *,
    include_failure_context: bool = True,
) -> ImproveTriageResult:
    classification, rationale = classify_blocked_issue(issue, comments)
    classification_counts = recent_classification_counts(client)
    # Only escalate to a system-fix follow-up task once the same classification
    # has appeared at least 3 times in recent issues, giving retries time to work.
    repeated_pattern = classification_counts.get(classification, 0) >= 3
    issue_title = str(issue.get("name", "task"))
    issue_kind = task_kind_for_issue(issue)

    if classification in {"infra_tooling", UNKNOWN_BLOCKED_CLASSIFICATION}:
        return ImproveTriageResult(
            classification=classification,
            certainty="high" if classification == "infra_tooling" else "low",
            reason_summary=rationale,
            recommended_action="human_attention",
            human_attention_required=True,
        )

    if repeated_pattern:
        follow_up = ImproveFollowUpSpec(
            task_kind="goal",
            title=f"Address repeated {classification_display_name(classification)} failures",
            goal_text=(
                f"Stabilize the autonomous workflow by addressing the repeated {classification_display_name(classification)} pattern "
                "showing up across recent tasks."
            ),
            handoff_reason=f"improve_pattern_{classification}",
            constraints_text=(
                f"- source_task_id: {issue.get('id')}\n"
                f"- repeated_classification: {classification}\n"
                "- focus on one bounded system-fix task rather than many task-specific children"
            ),
        )
        return ImproveTriageResult(
            classification=classification,
            certainty="high",
            reason_summary=f"{rationale} This same failure pattern has appeared repeatedly in recent work.",
            recommended_action="create_follow_up_goal",
            human_attention_required=False,
            follow_up=follow_up,
        )

    task_kind = "goal"
    goal_text = f"Resolve the blocked task '{issue_title}' by addressing the classified failure: {classification_display_name(classification)}."
    if classification == "scope_policy":
        goal_text = (
            f"Resolve the blocked task '{issue_title}' with a narrower, policy-compliant implementation that stays within allowed paths."
        )
    if classification == "verification_failure":
        goal_text = (
            f"Fix the implementation gap exposed by verification in '{issue_title}' so the verification task can pass on the next run."
        )
    if classification == "context_limit":
        goal_text = (
            f"The task '{issue_title}' was too large for a single kodo run. "
            "Break the work into a smaller, bounded scope and re-attempt with a focused goal that can be completed within one context window."
        )
    if classification == "dependency_missing":
        goal_text = (
            f"The task '{issue_title}' failed because a required dependency was not available. "
            "Install or configure the missing dependency in the repo bootstrap, then re-attempt the original task."
        )

    prior_context = _summarise_prior_failures(client, str(issue.get("id", ""))) if include_failure_context else ""
    constraints_lines = [
        f"- source_task_id: {issue.get('id')}",
        f"- source_task_kind: {issue_kind}",
        f"- classification: {classification}",
        f"- rationale: {rationale}",
    ]
    if prior_context:
        constraints_lines.append(f"- prior_failure_context: {prior_context}")
    # Scope policy learning: inject the changed_files from the prior execution as
    # avoid_paths so the resolve task knows which files went out-of-scope.
    if classification == "scope_policy":
        task_artifact = UsageStore().get_task_artifact(str(issue.get("id", "")))
        if task_artifact:
            changed = [str(f) for f in task_artifact.get("changed_files", []) if f]
            if changed:
                constraints_lines.append(f"- avoid_paths: {', '.join(sorted(changed))}")
                constraints_lines.append(
                    "- reason: these paths were modified in the blocked run and triggered a scope policy violation; stay within the allowed paths"
                )
    follow_up = ImproveFollowUpSpec(
        task_kind=task_kind,
        title=f"Resolve blocked {issue_title}",
        goal_text=goal_text,
        handoff_reason=f"improve_triage_{classification}",
        constraints_text="\n".join(constraints_lines),
    )
    return ImproveTriageResult(
        classification=classification,
        certainty="high" if classification != "parse_config" else "medium",
        reason_summary=rationale,
        recommended_action=f"create_follow_up_{task_kind}",
        human_attention_required=False,
        follow_up=follow_up,
    )


def default_repo_key(service: ExecutionService) -> str:
    return next(iter(service.settings.repos.keys()))


def proposal_repo_keys(service: ExecutionService) -> list[str]:
    return [key for key, cfg in service.settings.repos.items() if getattr(cfg, "propose_enabled", True)]


def _extract_repo_key(issue: dict[str, Any], service: ExecutionService) -> str:
    """Return the repo key from an issue's label or fall back to the service default."""
    labels = issue_label_names(issue)
    repo_label = next(
        (lbl.split(":", 1)[1].strip() for lbl in labels if lbl.lower().startswith("repo:")),
        None,
    )
    return repo_label or default_repo_key(service)


def allowed_paths_for_repo(repo_key: str) -> list[str]:
    if repo_key.strip().lower() in {"controlplane", "control-plane"}:
        return ["src/", "tests/"]
    return []


def issue_execution_target(issue: dict[str, Any], service: ExecutionService) -> tuple[str, str, list[str]]:
    description = issue_description_text(issue).strip()
    if description:
        try:
            metadata = TaskParser().parse(description).execution_metadata
            repo_key = str(metadata.get("repo", "")).strip()
            base_branch = str(metadata.get("base_branch", "")).strip()
            if repo_key in service.settings.repos and base_branch:
                allowed_paths = [str(path).strip() for path in metadata.get("allowed_paths", []) if str(path).strip()]
                return repo_key, base_branch, allowed_paths or allowed_paths_for_repo(repo_key)
        except ValueError as exc:
            _logger.debug("Failed to parse task metadata: %s", exc)

    repo_key = default_repo_key(service)
    repo_cfg = service.settings.repos[repo_key]
    return repo_key, repo_cfg.default_branch, allowed_paths_for_repo(repo_key)


def existing_issue_names(client: PlaneClient, *, issues: list[dict[str, Any]] | None = None) -> set[str]:
    names: set[str] = set()
    for issue in issues or client.list_issues():
        state_name = issue_status_name(issue).strip().lower()
        if state_name in {"done", "cancelled"}:
            continue
        name = str(issue.get("name", "")).strip().lower()
        if name:
            names.add(name)
    return names


def build_follow_up_description(
    *,
    service: ExecutionService,
    source_role: str,
    task_kind: str,
    original_issue: dict[str, Any],
    goal_text: str,
    handoff_reason: str,
    constraints_text: str | None = None,
) -> str:
    repo_key, base_branch, allowed_paths = issue_execution_target(original_issue, service)
    lines = [
        "## Execution",
        f"repo: {repo_key}",
        f"base_branch: {base_branch}",
        "mode: goal",
    ]
    if allowed_paths:
        lines.append("allowed_paths:")
        for path in allowed_paths:
            lines.append(f"  - {path}")
    lines.extend(["", "## Goal", goal_text])
    if constraints_text:
        lines.extend(["", "## Constraints", constraints_text])
    lines.extend(
        [
            "",
            "## Context",
            f"- original_task_id: {original_issue.get('id')}",
            f"- original_task_title: {original_issue.get('name', 'Untitled')}",
            f"- source_worker_role: {source_role}",
            f"- source_task_kind: {task_kind_for_issue(original_issue)}",
            f"- follow_up_task_kind: {task_kind}",
            f"- handoff_reason: {handoff_reason}",
        ]
    )
    return "\n".join(lines).strip()


def create_follow_up_task(
    client: PlaneClient,
    service: ExecutionService,
    *,
    source_role: str,
    task_kind: str,
    original_issue: dict[str, Any],
    title: str,
    goal_text: str,
    handoff_reason: str,
    constraints_text: str | None = None,
    state: str = "Ready for AI",
) -> dict[str, Any]:
    description = build_follow_up_description(
        service=service,
        source_role=source_role,
        task_kind=task_kind,
        original_issue=original_issue,
        goal_text=goal_text,
        handoff_reason=handoff_reason,
        constraints_text=constraints_text,
    )
    created = client.create_issue(
        name=title,
        description=description,
        state=state,
        label_names=[f"task-kind: {task_kind}", f"source: {source_role}-worker"],
    )
    client.comment_issue(
        str(created.get("id")),
        render_worker_comment(
            f"[{worker_title(source_role)}] Follow-up task created",
            [
                f"source_task_id: {original_issue.get('id')}",
                f"source_task_title: {original_issue.get('name', 'Untitled')}",
                f"source_worker_role: {source_role}",
                f"task_kind: {task_kind}",
                f"handoff_reason: {handoff_reason}",
                "result_status: ready_for_ai",
            ],
        ),
    )
    return created


def create_follow_up_task_if_missing(
    client: PlaneClient,
    service: ExecutionService,
    *,
    existing_names: set[str],
    existing_follow_ups: set[tuple[str, str, str]],
    source_role: str,
    task_kind: str,
    original_issue: dict[str, Any],
    title: str,
    goal_text: str,
    handoff_reason: str,
    constraints_text: str | None = None,
    state: str = "Ready for AI",
) -> dict[str, Any] | None:
    normalized = title.strip().lower()
    if normalized in existing_names:
        return None
    duplicate_key = (str(original_issue.get("id")), task_kind.strip().lower(), handoff_reason.strip().lower())
    if duplicate_key in existing_follow_ups:
        return None
    created = create_follow_up_task(
        client,
        service,
        source_role=source_role,
        task_kind=task_kind,
        original_issue=original_issue,
        title=title,
        goal_text=goal_text,
        handoff_reason=handoff_reason,
        constraints_text=constraints_text,
        state=state,
    )
    existing_names.add(normalized)
    existing_follow_ups.add(duplicate_key)
    return created


def recent_report_dirs(service: ExecutionService, limit: int = 8) -> list[Path]:
    report_root = Path(service.settings.report_root)
    if not report_root.exists():
        return []
    return sorted(
        [path for path in report_root.iterdir() if path.is_dir()],
        key=lambda path: path.name,
        reverse=True,
    )[:limit]


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


_SCAN_EXTENSIONS = {".py", ".ts", ".js", ".go", ".rs"}
_WIP_COMMIT_WORDS = ("wip", "hack", "fixme", "todo", "temp", "workaround", "broken", "kludge", "bandaid")
_ACTIONABLE_MARKERS = ("# TODO", "# FIXME", "# HACK", "# XXX", "// TODO", "// FIXME", "// HACK")
_LARGE_FILE_LINES = 300
_LARGE_FUNCTION_LINES = 50
_MIN_TYPE_IGNORE_TO_FLAG = 3
_MIN_UNTYPED_PUBLIC_FNS_TO_FLAG = 5


def _py_source_files(repo_path: Path) -> list[Path]:
    """All Python source files under src/, lib/, or root — excluding hidden dirs and tests."""
    roots = [repo_path / d for d in ("src", "lib") if (repo_path / d).is_dir()]
    if not roots:
        roots = [repo_path]
    seen: set[Path] = set()
    result: list[Path] = []
    for root in roots:
        for p in sorted(root.rglob("*.py")):
            if p in seen or any(part.startswith(".") for part in p.parts):
                continue
            seen.add(p)
            result.append(p)
    return result


def _run_tool(cmd: list[str], cwd: Path, timeout: int = 30) -> str:
    """Run a subprocess tool, return stdout. Empty string on any failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout, check=False)
        return result.stdout
    except Exception as exc:
        _logger.debug("Tool command failed %s: %s", cmd, exc)
        return ""


def _safety_findings(repo_path: Path) -> list[dict[str, str]]:
    """Find runtime safety issues: subprocess without timeout, shell=True, eval/exec."""
    import collections as _col

    no_timeout: list[str] = []
    shell_true: list[str] = []
    eval_exec: list[str] = []

    for path in _py_source_files(repo_path):
        rel = str(path.relative_to(repo_path))
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, OSError) as exc:
            _logger.debug("Skipping %s during safety scan: %s", path, exc)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            kw_names = {k.arg for k in node.keywords}
            if any(s in fn for s in ("subprocess.run", "subprocess.call", "subprocess.check", "Popen")):
                if "timeout" not in kw_names:
                    no_timeout.append(f"{rel}:{node.lineno}")
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    shell_true.append(f"{rel}:{node.lineno}")
            if fn in ("eval", "exec"):
                eval_exec.append(f"{rel}:{node.lineno}")

    findings: list[dict[str, str]] = []
    if no_timeout:
        by_file: dict[str, int] = _col.Counter(loc.split(":")[0] for loc in no_timeout)
        worst = sorted(by_file, key=lambda f: -by_file[f])[:3]
        sample = "; ".join(no_timeout[:4])
        findings.append({
            "kind": "goal",
            "title": f"Add timeout to {len(no_timeout)} subprocess call(s) missing one",
            "goal": (
                f"Found {len(no_timeout)} `subprocess` calls without a `timeout=` argument — these can hang the worker loop indefinitely. "
                f"Top offending files: {', '.join(worst)}. Sample locations: {sample}. "
                "Add appropriate timeouts to all uncovered calls."
            ),
            "constraints": "- source: ast safety scan\n- add timeout to every subprocess call without one\n- choose timeouts appropriate to the operation (short for quick commands, longer for builds)\n- do not change call behaviour",
            "note": f"- safety: {len(no_timeout)} subprocess calls without timeout in {len(by_file)} file(s)",
        })
    if shell_true:
        sample = "; ".join(shell_true[:3])
        findings.append({
            "kind": "goal",
            "title": f"Remove shell=True from {len(shell_true)} subprocess call(s)",
            "goal": (
                f"Found {len(shell_true)} subprocess calls with `shell=True` ({sample}). "
                "This passes commands through the shell, enabling injection if any part of the command includes untrusted input. "
                "Replace with list-form arguments where possible."
            ),
            "constraints": "- source: ast safety scan\n- convert shell=True calls to list-form args\n- verify behaviour is preserved after each change\n- only use shell=True where strictly necessary and document why",
            "note": f"- safety: {len(shell_true)} shell=True subprocess call(s)",
        })
    if eval_exec:
        findings.append({
            "kind": "goal",
            "title": f"Eliminate eval()/exec() usage ({len(eval_exec)} location(s))",
            "goal": f"Found {len(eval_exec)} use(s) of `eval()`/`exec()`: {'; '.join(eval_exec[:3])}. Replace with safe alternatives.",
            "constraints": "- source: ast safety scan\n- replace each with a safe alternative\n- document any case where dynamic execution is genuinely required",
            "note": f"- safety: {len(eval_exec)} eval/exec usage(s)",
        })
    return findings


def _dead_code_findings(repo_path: Path) -> list[dict[str, str]]:
    """Run vulture if available; otherwise detect private functions never called in their own file."""
    # Try vulture first
    vulture_out = _run_tool(["vulture", ".", "--min-confidence", "80"], cwd=repo_path, timeout=30)
    if vulture_out.strip():
        lines = [ln for ln in vulture_out.splitlines() if "unused" in ln.lower()][:20]
        if lines:
            by_file: dict[str, int] = {}
            for line in lines:
                f = line.split(":")[0]
                by_file[f] = by_file.get(f, 0) + 1
            worst = sorted(by_file, key=lambda f: -by_file[f])[:3]
            return [{
                "kind": "goal",
                "title": f"Remove dead code flagged by vulture ({len(lines)} item(s))",
                "goal": f"Vulture found {len(lines)} unused code items. Top files: {', '.join(worst)}. Run `vulture . --min-confidence 80` for the full list and remove or justify each unused item.",
                "constraints": "- source: vulture dead code scan\n- remove genuinely unused code\n- if an item is used dynamically (plugin, hook, __all__) add a vulture whitelist entry\n- do not remove public API used by external callers",
                "note": f"- dead code: vulture flagged {len(lines)} item(s)",
            }]

    # Fallback: private functions in a file never called within that same file
    dead: list[str] = []
    for path in _py_source_files(repo_path):
        rel = str(path.relative_to(repo_path))
        try:
            src = path.read_text(errors="replace")
            tree = ast.parse(src)
        except (SyntaxError, OSError) as exc:
            _logger.debug("Skipping %s during dead-code scan: %s", path, exc)
            continue
        defined_private = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_") and not node.name.startswith("__")
        }
        called = {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for name in sorted(defined_private - called):
            dead.append(f"{rel}: {name}")

    if len(dead) >= 3:
        return [{
            "kind": "goal",
            "title": f"Remove {len(dead)} potentially dead private function(s)",
            "goal": f"These private functions are defined but never referenced within their own file: {', '.join(dead[:6])}. Verify they are not used externally and remove the ones that are truly dead.",
            "constraints": "- source: ast dead code scan\n- verify each function is not called via dynamic dispatch or test\n- remove confirmed dead code\n- install `vulture` for a more thorough scan",
            "note": f"- dead code: {len(dead)} private function(s) unreferenced in own file",
        }]
    return []


def _type_coverage_findings(repo_path: Path) -> list[dict[str, str]]:
    """Run ty for type errors; fall back to AST scan for annotation gaps and type: ignore debt."""
    import re as _re
    import collections as _col

    findings: list[dict[str, str]] = []

    # ty: fast Rust-based type checker (Astral, same team as ruff/uv)
    ty_out = _run_tool(["ty", "check", "."], cwd=repo_path, timeout=60)
    if ty_out.strip():
        error_lines = [ln for ln in ty_out.splitlines() if "error[" in ln]
        if error_lines:
            by_file: _col.Counter[str] = _col.Counter()
            codes: _col.Counter[str] = _col.Counter()
            for line in error_lines:
                m = re.search(r"-->\s*([^:]+):", line)
                if m:
                    by_file[m.group(1).strip()] += 1
                cm = re.search(r"error\[([^\]]+)\]", line)
                if cm:
                    codes[cm.group(1)] += 1
            worst = [f for f, _ in by_file.most_common(3)]
            top_codes = ", ".join(f"{c}({n})" for c, n in codes.most_common(4))
            findings.append({
                "kind": "goal",
                "title": f"Fix {len(error_lines)} type error(s) found by ty",
                "goal": (
                    f"`ty check` found {len(error_lines)} type errors. "
                    f"Top error codes: {top_codes}. "
                    f"Most affected files: {', '.join(worst)}. "
                    "Run `ty check .` for the full list and fix each error."
                ),
                "constraints": (
                    "- source: ty static type analysis\n"
                    "- fix type errors without changing runtime behaviour\n"
                    "- run `ty check .` after each file to verify progress\n"
                    "- do not suppress errors with # type: ignore unless genuinely unavoidable"
                ),
                "note": f"- type errors: ty found {len(error_lines)} error(s) ({top_codes})",
            })

    # AST: public functions without return annotations (ty doesn't report these as errors)
    untyped_by_file: dict[str, list[str]] = _col.defaultdict(list)
    type_ignore_by_file: dict[str, int] = {}
    for path in _py_source_files(repo_path):
        rel = str(path.relative_to(repo_path))
        try:
            src = path.read_text(errors="replace")
            tree = ast.parse(src)
        except (SyntaxError, OSError) as exc:
            _logger.debug("Skipping %s during type scan: %s", path, exc)
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_") and node.returns is None:
                    untyped_by_file[rel].append(f"{node.name}():{node.lineno}")
        count = len(_re.findall(r"#\s*type:\s*ignore", src))
        if count:
            type_ignore_by_file[rel] = count

    total_untyped = sum(len(v) for v in untyped_by_file.values())
    if total_untyped >= _MIN_UNTYPED_PUBLIC_FNS_TO_FLAG:
        worst_u = sorted(untyped_by_file, key=lambda f: -len(untyped_by_file[f]))[:3]
        sample = "; ".join(fn for w in worst_u for fn in untyped_by_file[w][:2])
        findings.append({
            "kind": "goal",
            "title": f"Add return type annotations to {total_untyped} public function(s)",
            "goal": (
                f"{total_untyped} public functions are missing return type annotations across {len(untyped_by_file)} file(s). "
                f"Top files: {', '.join(worst_u)}. Sample: {sample}. "
                "Add return annotations and verify with `ty check .`."
            ),
            "constraints": "- source: ast type coverage scan\n- add return type annotations only — do not change logic\n- verify with `ty check .`\n- use Optional/Union where appropriate",
            "note": f"- type coverage: {total_untyped} public functions without return annotation",
        })
    total_ignores = sum(type_ignore_by_file.values())
    if total_ignores >= _MIN_TYPE_IGNORE_TO_FLAG:
        worst_i = sorted(type_ignore_by_file, key=lambda f: -type_ignore_by_file[f])[:3]
        findings.append({
            "kind": "goal",
            "title": f"Resolve {total_ignores} # type: ignore suppression(s)",
            "goal": f"Found {total_ignores} `# type: ignore` suppressions in: {', '.join(worst_i)}. Fix the underlying type issue for each so the suppression can be removed. Verify with `ty check .`.",
            "constraints": "- source: ast type coverage scan\n- fix the root type issue for each suppression\n- do not simply broaden the type\n- verify with `ty check .`",
            "note": f"- type debt: {total_ignores} type: ignore suppression(s) in {len(type_ignore_by_file)} file(s)",
        })
    return findings


def _recursion_findings(repo_path: Path) -> list[dict[str, str]]:
    """Find direct recursion without obvious base case, and while-True loops without break."""
    risky_recursion: list[str] = []
    infinite_loops: list[str] = []

    for path in _py_source_files(repo_path):
        rel = str(path.relative_to(repo_path))
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, OSError) as exc:
            _logger.debug("Skipping %s during recursion scan: %s", path, exc)
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls_self = any(
                    (isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == node.name)
                    for child in ast.walk(node)
                )
                has_return_with_value = any(
                    isinstance(child, ast.Return) and child.value is not None
                    for child in ast.walk(node)
                )
                if calls_self and not has_return_with_value:
                    risky_recursion.append(f"{rel}:{node.lineno} {node.name}()")
            if isinstance(node, ast.While):
                is_infinite = isinstance(node.test, ast.Constant) and node.test.value is True
                has_break = any(isinstance(child, ast.Break) for child in ast.walk(node))
                if is_infinite and not has_break:
                    infinite_loops.append(f"{rel}:{node.lineno}")

    findings: list[dict[str, str]] = []
    if risky_recursion:
        findings.append({
            "kind": "goal",
            "title": f"Review {len(risky_recursion)} recursive function(s) without clear base case",
            "goal": f"These functions call themselves but have no return-with-value path (possible missing base case or stack overflow risk): {'; '.join(risky_recursion[:4])}. Verify each has a correct termination condition or convert to iteration.",
            "constraints": "- source: ast recursion scan\n- verify or add base case for each recursive function\n- consider converting deep recursion to iterative form\n- add tests that exercise the base case",
            "note": f"- recursion: {len(risky_recursion)} function(s) with possible missing base case",
        })
    if infinite_loops:
        findings.append({
            "kind": "goal",
            "title": f"Audit {len(infinite_loops)} while-True loop(s) without break",
            "goal": f"`while True` loops without a `break` statement at: {'; '.join(infinite_loops[:4])}. Verify each has a reachable exit path (e.g. via exception, sys.exit, or return) or add an explicit break/termination condition.",
            "constraints": "- source: ast loop scan\n- verify each loop has a clear exit path\n- add explicit termination condition where missing\n- add a comment explaining the exit mechanism",
            "note": f"- runtime: {len(infinite_loops)} while-True loop(s) without break",
        })
    return findings


def _ast_complexity_findings(repo_path: Path) -> list[dict[str, str]]:
    """Scan all Python source files for large files and long functions."""
    findings: list[dict[str, str]] = []
    candidates: list[tuple[int, Path, list[tuple[str, int]]]] = []

    src_roots = [repo_path / d for d in ("src", "lib", ".") if (repo_path / d).is_dir()]
    seen: set[Path] = set()
    for root in src_roots:
        for path in sorted(root.rglob("*.py")):
            if path in seen or any(part.startswith(".") for part in path.parts):
                continue
            seen.add(path)
            try:
                source = path.read_text(errors="replace")
                tree = ast.parse(source)
            except (SyntaxError, OSError) as exc:
                _logger.debug("Skipping %s during complexity scan: %s", path, exc)
                continue
            line_count = len(source.splitlines())
            large_fns: list[tuple[str, int]] = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    length = getattr(node, "end_lineno", node.lineno) - node.lineno
                    if length >= _LARGE_FUNCTION_LINES:
                        large_fns.append((node.name, length))
            large_fns.sort(key=lambda x: -x[1])
            if line_count >= _LARGE_FILE_LINES or large_fns:
                candidates.append((line_count, path, large_fns))

    candidates.sort(key=lambda x: -x[0])
    for line_count, path, large_fns in candidates[:4]:
        rel = str(path.relative_to(repo_path))
        fn_desc = ", ".join(f"`{name}` ({lines}L)" for name, lines in large_fns[:3])
        if line_count >= _LARGE_FILE_LINES and large_fns:
            title = f"Decompose {path.name} ({line_count}L, {len(large_fns)} oversized function(s))"
            goal = (
                f"`{rel}` has grown to {line_count} lines. "
                f"The largest functions are: {fn_desc}. "
                "Identify the most cohesive subset of functionality and extract it into a dedicated module. "
                "Choose one bounded extraction — not a full rewrite."
            )
            constraints = (
                f"- source: ast complexity scan\n"
                f"- target: {rel} ({line_count} lines)\n"
                f"- extract one coherent concern into a new module\n"
                f"- all existing tests must stay green\n"
                f"- add tests for the extracted module"
            )
            note = f"- complexity: {path.name} {line_count}L, functions: {fn_desc}"
        elif large_fns:
            title = f"Simplify oversized functions in {path.name}"
            goal = (
                f"The following functions in `{rel}` are too long to reason about easily: {fn_desc}. "
                "Refactor the longest one by extracting well-named helpers. Do not change observable behaviour."
            )
            constraints = (
                f"- source: ast complexity scan\n"
                f"- target: {rel}\n"
                f"- refactor one function at a time\n"
                f"- do not change public API or behaviour\n"
                f"- tests must stay green"
            )
            note = f"- complexity: oversized functions in {path.name}: {fn_desc}"
        else:
            title = f"Reduce size of {path.name} ({line_count}L)"
            goal = (
                f"`{rel}` is {line_count} lines with no clear split yet. "
                "Identify the best extraction boundary and move one cohesive section into its own module."
            )
            constraints = (
                f"- source: ast complexity scan\n"
                f"- target: {rel}\n"
                f"- extract one cohesive section\n"
                f"- tests must stay green"
            )
            note = f"- complexity: {path.name} {line_count}L"

        findings.append({"kind": "goal", "title": title, "goal": goal, "constraints": constraints, "note": note})

    return findings


def _ruff_findings(repo_path: Path) -> list[dict[str, str]]:
    """Run ruff on the repo and surface files with the most violations."""
    import collections
    output = _run_tool(["ruff", "check", "--output-format=json", "."], cwd=repo_path, timeout=30)
    if not output.strip():
        return []
    try:
        violations = json.loads(output)
    except json.JSONDecodeError:
        return []
    by_file: dict[str, list[dict]] = collections.defaultdict(list)
    for v in violations:
        filename = v.get("filename", "")
        if filename:
            rel = str(Path(filename).relative_to(repo_path)) if Path(filename).is_absolute() else filename
            by_file[rel].append(v)

    findings: list[dict[str, str]] = []
    for rel, viols in sorted(by_file.items(), key=lambda x: -len(x[1]))[:3]:
        count = len(viols)
        codes = sorted({v.get("code", "") for v in viols if v.get("code")})[:6]
        code_str = ", ".join(codes)
        findings.append({
            "kind": "goal",
            "title": f"Fix {count} lint violation(s) in {Path(rel).name}",
            "goal": f"Fix the {count} ruff violations in `{rel}`. Violation codes: {code_str}. Run `ruff check {rel}` to see the full list.",
            "constraints": f"- source: ruff static analysis\n- target: {rel}\n- fix all violations in one pass\n- do not change behaviour, only style/correctness",
            "note": f"- ruff: {count} violation(s) in {rel} ({code_str})",
        })
    return findings


def _all_todo_findings(repo_path: Path) -> list[dict[str, str]]:
    """Scan ALL source files for TODO/FIXME, return worst offenders not already in recent-change findings."""
    import collections
    counts: collections.Counter[str] = collections.Counter()
    samples: dict[str, list[str]] = {}
    src_roots = [repo_path / d for d in ("src", "lib") if (repo_path / d).is_dir()]
    if not src_roots:
        src_roots = [repo_path]
    seen: set[Path] = set()
    for root in src_roots:
        for path in sorted(root.rglob("*.py")):
            if path in seen or any(part.startswith(".") for part in path.parts):
                continue
            seen.add(path)
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            rel = str(path.relative_to(repo_path))
            for i, line in enumerate(lines[:500], 1):
                if any(m in line.upper() for m in ("# TODO", "# FIXME", "# HACK", "# XXX")):
                    counts[rel] += 1
                    samples.setdefault(rel, []).append(f"line {i}: {line.strip()[:70]}")

    findings: list[dict[str, str]] = []
    for rel, count in counts.most_common(2):
        top = "; ".join(samples[rel][:3])
        findings.append({
            "kind": "goal",
            "title": f"Address {count} TODO/FIXME marker(s) in {Path(rel).name}",
            "goal": f"Resolve the {count} outstanding TODO/FIXME items in `{rel}`. Top markers: {top}.",
            "constraints": f"- source: full codebase TODO scan\n- target: {rel}\n- address markers in order of impact\n- do not leave new TODOs behind",
            "note": f"- todo scan: {count} marker(s) in {rel}",
        })
    return findings


def _scan_file_for_signals(repo_path: Path, rel_path: str) -> list[dict[str, str]]:
    """Read a source file and return findings for actionable inline markers and stubs."""
    findings: list[dict[str, str]] = []
    file_path = repo_path / rel_path
    if not file_path.is_file() or file_path.suffix not in _SCAN_EXTENSIONS:
        return findings
    try:
        lines = file_path.read_text(errors="replace").splitlines()[:400]
    except OSError:
        return findings

    todos: list[str] = []
    stubs: list[int] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if any(m in stripped.upper() for m in _ACTIONABLE_MARKERS):
            todos.append(f"line {i}: {stripped[:80]}")
        if stripped in ("raise NotImplementedError", "raise NotImplementedError()"):
            stubs.append(i)

    if todos:
        findings.append({
            "kind": "goal",
            "title": f"Resolve TODO/FIXME markers in {Path(rel_path).name}",
            "goal": f"Address the outstanding TODO/FIXME items in `{rel_path}`: {'; '.join(todos[:3])}",
            "constraints": f"- source: retained code scan\n- focus only on {rel_path}\n- do not expand into unrelated changes",
            "note": f"- code scan signal: {len(todos)} actionable marker(s) in {rel_path}",
        })
    if stubs:
        findings.append({
            "kind": "goal",
            "title": f"Implement unfinished stub in {Path(rel_path).name}",
            "goal": f"Implement the unfinished stub (`raise NotImplementedError`) at line(s) {', '.join(str(n) for n in stubs[:3])} in `{rel_path}`.",
            "constraints": f"- source: retained code scan\n- implement the stub in {rel_path} and add tests\n- do not expand scope",
            "note": f"- code scan signal: unimplemented stub in {rel_path}",
        })
    return findings


def _find_untested_module(repo_path: Path) -> str | None:
    """Return the first source module that has no corresponding test file, or None."""
    test_root = next(
        (repo_path / d for d in ("tests", "test") if (repo_path / d).is_dir()), None
    )
    if not test_root:
        return None
    tested: set[str] = {f.stem.removeprefix("test_") for f in test_root.rglob("test_*.py")}
    for src_dir in ("src", "lib", "."):
        sd = repo_path / src_dir
        if not sd.is_dir():
            continue
        for src_file in sorted(sd.rglob("*.py")):
            if src_file.name.startswith("_") or src_file.name == "conftest.py":
                continue
            if src_file.stem not in tested:
                return str(src_file.relative_to(repo_path))
    return None


def discover_improvement_candidates(
    service: ExecutionService,
    *,
    repo_key: str,
    base_branch: str | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    findings: list[dict[str, str]] = []
    report_notes: list[str] = []
    kodo_adapter_text = read_text_if_exists(Path("src/control_plane/adapters/kodo/adapter.py"))
    kodo_project_flag_present = "--project" in kodo_adapter_text

    for run_dir in recent_report_dirs(service):
        result_summary = read_text_if_exists(run_dir / "result_summary.md").lower()
        kodo_stderr = read_text_if_exists(run_dir / "kodo_stderr.log").lower()
        failure_json = read_text_if_exists(run_dir / "failure.json").lower()
        request_json = read_text_if_exists(run_dir / "request.json")

        task_title = "recent task"
        if request_json:
            try:
                payload = json.loads(request_json)
                task_title = str(payload.get("task", {}).get("title") or task_title)
            except json.JSONDecodeError:
                task_title = "recent task"

        if (
            not kodo_project_flag_present
            and "unrecognized arguments:" in kodo_stderr
            and "/tmp/" in kodo_stderr
        ):
            findings.append(
                {
                    "kind": "goal",
                    "title": "Adapt wrapper to installed Kodo CLI contract",
                    "goal": "Update the wrapper to match the installed Kodo CLI argument contract and add a regression test for the command builder.",
                    "constraints": "- source: retained report analysis\n- focus on the Kodo adapter and its tests",
                    "note": f"- report signal: Kodo CLI contract mismatch observed in {run_dir.name}",
                }
            )

        if "execution=failed" in result_summary and "validation=passed" in result_summary and "changed_files=0" in result_summary:
            findings.append(
                {
                    "kind": "goal",
                    "title": "Handle no-op failed Kodo executions explicitly",
                    "goal": (
                        f"Improve worker/execution handling so failed no-op Kodo runs for '{task_title}' are classified and surfaced clearly "
                        "instead of only ending as generic blocked tasks."
                    ),
                    "constraints": "- source: retained report analysis\n- focus on execution summaries, classification, and operator feedback",
                    "note": f"- report signal: no-op failed execution observed in {run_dir.name}",
                }
            )

        if "429 too many requests" in failure_json:
            findings.append(
                {
                    "kind": "goal",
                    "title": "Reduce Plane API pressure during worker execution",
                    "goal": "Reduce Plane API pressure during worker execution and retries so task fetch/comment flows do not degrade into avoidable 429s.",
                    "constraints": "- source: retained failure artifact\n- focus on request reduction, retries, and worker fetch patterns",
                    "note": f"- report signal: Plane rate limiting observed in {run_dir.name}",
                }
            )

    repo_cfg = service.settings.repos[repo_key]
    target_branch = base_branch or repo_cfg.default_branch

    if repo_cfg.local_path:
        repo_path = Path(repo_cfg.local_path)
        service.git.checkout_base(repo_path, target_branch)
        _inspect_repo(service, repo_path, repo_key, target_branch, findings, report_notes, local=True)
    else:
        workspace_path = service.workspace.create()
        try:
            repo_path = service.git.clone(repo_cfg.clone_url, workspace_path)
            service.git.checkout_base(repo_path, target_branch)
            _inspect_repo(service, repo_path, repo_key, target_branch, findings, report_notes, local=False)
        finally:
            service.workspace.cleanup(workspace_path)

    unique: dict[str, dict[str, str]] = {}
    for finding in findings:
        unique.setdefault(finding["title"].strip().lower(), finding)
    return list(unique.values())[:10], report_notes


def _inspect_repo(
    service: ExecutionService,
    repo_path: Path,
    repo_key: str,
    target_branch: str,
    findings: list[dict[str, str]],
    report_notes: list[str],
    *,
    local: bool,
) -> None:
    """Deep repo inspection: git history, file content scan, coverage gaps."""
    label = f"{repo_key} @ {target_branch}" + (" (local)" if local else "")
    report_notes.append(f"- inspected repo: {label}")

    top_level = sorted(p.name for p in repo_path.iterdir() if p.name != ".git")
    report_notes.append(f"- top-level entries: {', '.join(top_level[:10]) or '(none)'}")
    report_notes.append(f"- tests directory present: {'yes' if (repo_path / 'tests').exists() else 'no'}")
    report_notes.append(f"- docs directory present: {'yes' if (repo_path / 'docs').exists() else 'no'}")

    # More commits — surface WIP/hack patterns
    all_commits = service.git.recent_commits(repo_path, max_count=20)
    if all_commits:
        report_notes.append(f"- recent commits: {' | '.join(all_commits[:5])}")
    wip_commits = [c for c in all_commits if any(w in c.lower() for w in _WIP_COMMIT_WORDS)]
    if wip_commits:
        report_notes.append(f"- wip/hack commits: {' | '.join(wip_commits[:3])}")
        findings.append({
            "kind": "goal",
            "title": "Follow up on WIP/temporary commits",
            "goal": f"Address the incomplete or temporary work signalled by recent commits: {'; '.join(wip_commits[:2])}",
            "constraints": "- source: retained git analysis\n- complete or clean up the temporary work\n- do not expand scope beyond what the commits indicate",
            "note": f"- git signal: WIP/hack pattern in recent commits ({len(wip_commits)} found)",
        })

    # More recently changed files — scan contents
    recent_files = service.git.recent_changed_files(repo_path, max_count=8)
    if recent_files:
        report_notes.append(f"- recently changed files: {', '.join(recent_files[:6])}")
    for rel_path in recent_files[:5]:
        for finding in _scan_file_for_signals(repo_path, rel_path):
            findings.append(finding)
            report_notes.append(f"- {finding['note'].lstrip('- ')}")

    # Test coverage gaps — scan all source modules
    untested = _find_untested_module(repo_path)
    if untested:
        findings.append({
            "kind": "test",
            "title": f"Add tests for {Path(untested).name}",
            "goal": f"Write meaningful tests for `{untested}` — no test file currently covers this module.",
            "constraints": f"- source: retained code scan\n- focus on {untested}\n- verify actual behaviour, not just happy path",
            "note": f"- coverage gap: no tests found for {untested}",
        })
        report_notes.append(f"- coverage gap: {untested}")

    # Deep static analysis
    for scanner in (
        _ast_complexity_findings,
        _safety_findings,
        _recursion_findings,
        _dead_code_findings,
        _type_coverage_findings,
        _ruff_findings,
        _all_todo_findings,
    ):
        for finding in scanner(repo_path):
            findings.append(finding)
            report_notes.append(f"- {finding['note'].lstrip('- ')}")


def evidence_lines_from_notes(notes: list[str]) -> list[str]:
    values = [note.removeprefix("- ").strip() for note in notes if note.strip()]
    priorities = (
        "safety:",
        "dead code:",
        "recursion:",
        "runtime:",
        "complexity:",
        "ruff:",
        "type coverage:",
        "type debt:",
        "code scan signal:",
        "coverage gap:",
        "git signal:",
        "wip/hack commits:",
        "report signal:",
        "recently changed files:",
        "recent commits:",
        "inspected repo:",
        "top-level entries:",
        "tests directory present:",
        "docs directory present:",
    )

    def sort_key(line: str) -> tuple[int, str]:
        lowered = line.lower()
        for index, prefix in enumerate(priorities):
            if lowered.startswith(prefix):
                return (index, lowered)
        return (len(priorities), lowered)

    return sorted(values, key=sort_key)


def idle_board_target_area(evidence_lines: list[str]) -> str | None:
    for line in evidence_lines:
        lowered = line.lower()
        if lowered.startswith("recently changed files:"):
            paths = [part.strip() for part in line.split(":", 1)[1].split(",") if part.strip()]
            if paths:
                return paths[0]
    return None


def idle_board_recent_commit_hint(evidence_lines: list[str]) -> str | None:
    for line in evidence_lines:
        lowered = line.lower()
        if lowered.startswith("recent commits:"):
            commit_text = line.split(":", 1)[1].strip()
            if commit_text:
                return commit_text.split("|", 1)[0].strip()
    return None


def idle_board_fallback_proposal(*, repo_key: str, evidence_lines: list[str]) -> ProposalSpec:
    target_area = idle_board_target_area(evidence_lines)
    commit_hint = idle_board_recent_commit_hint(evidence_lines)
    title = "Implement next bounded repo improvement"
    goal_text = (
        "Inspect recent retained signals and recent repo history, choose one concrete bounded issue they point to, "
        "and implement exactly that improvement."
    )
    reason_summary = "The board is idle and no stronger grounded task signal is currently open."
    dedup_key = f"{repo_key}:idle_board:repo_scan"
    constraints_lines = [
        "- start from the evidence listed below before exploring elsewhere",
        "- choose exactly one evidence item as the source of truth and name it in your worker summary",
        "- use recent commit history and retained reports to choose the target area",
        "- make at most one bounded improvement in this task",
        "- if no strong grounded improvement survives inspection, leave the repo unchanged and report no_op",
        "- do not expand into unrelated refactors",
    ]
    if target_area:
        title = f"Implement bounded improvement in {target_area}"
        goal_text = (
            f"Start with `{target_area}` as the target area, inspect the retained evidence and recent history around it, "
            "then implement one concrete bounded improvement there."
        )
        reason_summary = f"Idle-board fallback anchored to recent change activity in {target_area}."
        dedup_key = f"{repo_key}:idle_board:{re.sub(r'[^a-z0-9]+', '_', target_area.lower()).strip('_')}"
        constraints_lines.insert(2, f"- prefer `{target_area}` unless the evidence directly proves another nearby file is the true fix location")
    if commit_hint:
        constraints_lines.insert(3 if target_area else 2, f"- recent commit anchor: {commit_hint}")
    return ProposalSpec(
        repo_key=repo_key,
        task_kind="goal",
        title=title,
        goal_text=goal_text,
        reason_summary=reason_summary,
        source_signal=f"{repo_key}:idle_board",
        confidence="medium",
        recommended_state="Ready for AI",
        handoff_reason="propose_idle_board_scan",
        dedup_key=dedup_key,
        constraints_text="\n".join(constraints_lines),
        evidence_lines=evidence_lines,
    )


def idle_board_evidence_is_strong(*, findings: list[dict[str, str]], evidence_lines: list[str]) -> bool:
    if findings:
        return True
    for line in evidence_lines:
        lowered = line.lower()
        if lowered.startswith("recent commits:") or lowered.startswith("recently changed files:") or lowered.startswith("report signal:"):
            return True
    return False


def proposal_specs_from_findings(
    findings: list[dict[str, str]],
    *,
    repo_key: str,
    signal_prefix: str,
) -> list[ProposalSpec]:
    proposals: list[ProposalSpec] = []
    for finding in findings:
        kind = finding["kind"]
        normalized_title = re.sub(r"[^a-z0-9]+", "_", finding["title"].strip().lower()).strip("_")
        confidence = "high" if "retained" in finding["constraints"] or "report" in finding["constraints"] else "medium"
        state = "Ready for AI" if confidence == "high" else "Backlog"
        proposals.append(
            ProposalSpec(
                repo_key=repo_key,
                task_kind=kind,
                title=finding["title"],
                goal_text=finding["goal"],
                reason_summary=finding["note"].lstrip("- ").strip(),
                source_signal=f"{repo_key}:{signal_prefix}:{kind}",
                confidence=confidence,
                recommended_state=state,
                handoff_reason=f"propose_{signal_prefix}_{kind}",
                dedup_key=f"{repo_key}:{signal_prefix}:{normalized_title}",
                constraints_text=finding["constraints"],
            )
        )
    return proposals


def build_proposal_candidates(
    client: PlaneClient,
    service: ExecutionService,
    *,
    repo_key: str | None = None,
    issues: list[dict[str, Any]] | None = None,
    classification_counts: dict[str, int] | None = None,
) -> tuple[list[ProposalSpec], list[str], bool]:
    repo_key = repo_key or default_repo_key(service)
    issues = issues or client.list_issues()
    board_idle = board_is_idle_for_proposals_from_issues(issues, repo_key=repo_key)
    open_goal_or_test = 0
    ready_or_running_goal_or_test = 0
    for issue in issues:
        if not is_open_issue(issue):
            continue
        if issue_task_kind(issue) not in {"goal", "test"}:
            continue
        open_goal_or_test += 1
        if issue_status_name(issue) in {"Ready for AI", "Running"}:
            ready_or_running_goal_or_test += 1
    notes = [
        f"repo: {repo_key}",
        f"board_idle: {str(board_idle).lower()}",
        f"open_goal_or_test_count: {open_goal_or_test}",
        f"ready_or_running_goal_or_test_count: {ready_or_running_goal_or_test}",
    ]
    proposals: list[ProposalSpec] = []

    counts = classification_counts or recent_classification_counts(client, issues=issues)
    for classification, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        if count < 2:
            continue
        display_name = classification_display_name(classification)
        if classification == "infra_tooling":
            proposals.append(
                ProposalSpec(
                    repo_key=repo_key,
                    task_kind="improve",
                    title="Investigate repeated infrastructure/tooling blockers",
                    goal_text="Investigate the recurring infrastructure/tooling blocker pattern and produce one bounded next step or explicit operator guidance.",
                    reason_summary=f"Repeated blocked-task pattern detected: {display_name} ({count} recent occurrences).",
                    source_signal=f"{repo_key}:blocked_pattern:{classification}",
                    confidence="medium",
                    recommended_state="Backlog",
                    handoff_reason=f"propose_pattern_{classification}",
                    dedup_key=f"{repo_key}:blocked_pattern:{classification}",
                    constraints_text=f"- repeated_classification: {classification}\n- occurrence_count: {count}\n- prefer diagnosis and clear operator guidance over broad changes",
                    human_attention_required=True,
                )
            )
        else:
            proposals.append(
                ProposalSpec(
                    repo_key=repo_key,
                    task_kind="goal",
                    title=f"Address repeated {display_name} failures",
                    goal_text=f"Stabilize the local autonomous workflow by addressing the repeated {display_name} pattern showing up across recent work.",
                    reason_summary=f"Repeated blocked-task pattern detected: {display_name} ({count} recent occurrences).",
                    source_signal=f"{repo_key}:blocked_pattern:{classification}",
                    confidence="high",
                    recommended_state="Ready for AI",
                    handoff_reason=f"propose_pattern_{classification}",
                    dedup_key=f"{repo_key}:blocked_pattern:{classification}",
                    constraints_text=f"- repeated_classification: {classification}\n- occurrence_count: {count}\n- keep the task focused on one bounded system-fix",
                )
            )
        notes.append(f"repeated_pattern: {classification} x{count}")

    findings, report_notes = discover_improvement_candidates(
        service,
        repo_key=repo_key,
        base_branch=service.settings.repos[repo_key].default_branch,
    )
    notes.extend(report_notes)

    # Point 3: Split oversized decompose findings into bounded subtasks so kodo
    # receives tractable scope rather than a multi-thousand-line file at once.
    split_findings: list[dict[str, str]] = []
    for finding in findings:
        split_findings.extend(_split_oversized_finding(finding))
    if len(split_findings) != len(findings):
        notes.append(f"sizing_gate: split {len(findings)} finding(s) into {len(split_findings)} bounded subtask(s)")
    findings = split_findings

    proposals.extend(proposal_specs_from_findings(findings, repo_key=repo_key, signal_prefix="repo_scan"))

    if board_idle and not proposals:
        evidence_lines = evidence_lines_from_notes(report_notes[:6])
        if idle_board_evidence_is_strong(findings=findings, evidence_lines=evidence_lines):
            proposals.append(idle_board_fallback_proposal(repo_key=repo_key, evidence_lines=evidence_lines))
            notes.append("fallback_proposal: idle_board evidence-anchored repo improvement")
        else:
            notes.append("fallback_suppressed: idle_board evidence too weak")

    # Point 1: Focus area scoring — proposals not matching configured focus_areas
    # are demoted to Backlog so the system works on what matters first.
    focus_areas = list(getattr(service.settings, "focus_areas", None) or [])
    if focus_areas:
        demoted = 0
        for proposal in proposals:
            if not _proposal_matches_focus_areas(proposal, focus_areas):
                proposal.recommended_state = "Backlog"
                if proposal.confidence == "high":
                    proposal.confidence = "medium"
                    demoted += 1
        if demoted:
            notes.append(f"focus_gate: demoted {demoted} proposal(s) to Backlog (no focus area match)")

    # Point 5: Self-repo proposals always go to Backlog — they need explicit
    # "self-modify: approved" before the goal watcher will auto-execute them.
    if _is_self_repo(repo_key, service):
        for proposal in proposals:
            if proposal.recommended_state == "Ready for AI":
                proposal.recommended_state = "Backlog"
        notes.append("self_repo_gate: self-repo proposals capped at Backlog (require self-modify:approved label)")

    unique: dict[str, ProposalSpec] = {}
    for proposal in proposals:
        unique.setdefault(proposal.dedup_key.strip().lower(), proposal)
    return list(unique.values()), notes, board_idle


def build_proposal_description(
    *,
    service: ExecutionService,
    proposal: ProposalSpec,
) -> str:
    repo_key = proposal.repo_key or default_repo_key(service)
    repo_cfg = service.settings.repos[repo_key]
    lines = [
        "## Execution",
        f"repo: {repo_key}",
        f"base_branch: {repo_cfg.default_branch}",
        "mode: goal",
    ]
    allowed_paths = allowed_paths_for_repo(repo_key)
    if allowed_paths and proposal.task_kind in {"goal", "test"}:
        lines.append("allowed_paths:")
        for path in allowed_paths:
            lines.append(f"  - {path}")
    lines.extend(["", "## Goal", proposal.goal_text])
    if proposal.constraints_text:
        lines.extend(["", "## Constraints", proposal.constraints_text])
    lines.extend(
        [
            "",
            "## Context",
            "- source_worker_role: propose",
            f"- follow_up_task_kind: {proposal.task_kind}",
            f"- handoff_reason: {proposal.handoff_reason}",
            f"- source_signal: {proposal.source_signal}",
            f"- confidence: {proposal.confidence}",
            f"- proposal_dedup_key: {proposal.dedup_key}",
        ]
    )
    if proposal.evidence_lines:
        lines.extend(["", "## Evidence"])
        lines.extend(f"- {line}" for line in proposal.evidence_lines)
    return "\n".join(lines).strip()


def create_proposed_task_if_missing(
    client: PlaneClient,
    service: ExecutionService,
    *,
    proposal: ProposalSpec,
    existing_names: set[str],
    proposal_keys: set[str],
    memory: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    normalized_title = proposal.title.strip().lower()
    normalized_key = proposal.dedup_key.strip().lower()
    if normalized_title in existing_names or normalized_key in proposal_keys:
        return None
    if memory is not None and now is not None:
        if recently_proposed(memory, title=proposal.title, dedup_key=proposal.dedup_key, now=now):
            return None

    description = build_proposal_description(service=service, proposal=proposal)
    reason_label = re.sub(r"[^a-z0-9_]+", "_", proposal.source_signal.lower()).strip("_")
    created = client.create_issue(
        name=proposal.title,
        description=description,
        state=proposal.recommended_state,
        label_names=[f"task-kind: {proposal.task_kind}", "source: proposer", f"reason: {reason_label}"],
    )
    client.comment_issue(
        str(created.get("id")),
        render_worker_comment(
            PROPOSE_COMMENT_MARKER,
            [
                f"task_kind: {proposal.task_kind}",
                f"result_status: {proposal.recommended_state.lower().replace(' ', '_')}",
                f"source_signal: {proposal.source_signal}",
                f"confidence: {proposal.confidence}",
                f"dedup_key: {proposal.dedup_key}",
                f"handoff_reason: {proposal.handoff_reason}",
                f"human_attention_required: {str(proposal.human_attention_required).lower()}",
                f"reason: {proposal.reason_summary}",
            ],
        ),
    )
    existing_names.add(normalized_title)
    proposal_keys.add(normalized_key)
    if memory is not None and now is not None:
        record_proposed(memory, title=proposal.title, dedup_key=proposal.dedup_key, now=now)
    return created


def proposal_cooldown_active(memory: dict[str, Any], now: datetime) -> bool:
    raw = memory.get("last_proposal_at")
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return (now - last).total_seconds() < PROPOSAL_COOLDOWN_SECONDS


def proposal_quota_exhausted(memory: dict[str, Any], now: datetime) -> bool:
    cutoff = now.timestamp() - PROPOSAL_WINDOW_SECONDS
    timestamps = []
    for raw in memory.get("proposal_timestamps", []):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value >= cutoff:
            timestamps.append(value)
    memory["proposal_timestamps"] = timestamps
    return len(timestamps) >= MAX_PROPOSALS_PER_DAY


def _normalise_proposal_title(title: str) -> str:
    """Strip volatile metric suffixes so scan-drift doesn't create duplicate tasks.

    Examples:
      "Decompose main.py (2823L, 15 oversized function(s))" → "decompose main.py"
      "Fix 213 type error(s) found by ty"                   → "fix type error(s) found by ty"
      "Add timeout to 22 subprocess call(s) missing one"    → "add timeout to subprocess call(s) missing one"
    """
    s = title.strip().lower()
    # Remove parenthesised metric blocks: "(2823L, 15 oversized function(s))"
    s = re.sub(r"\s*\(\d+l[^)]*\)", "", s)
    # Remove leading counts before known nouns: "213 type error" → "type error"
    s = re.sub(r"\b\d+\s+(?=type error|subprocess|function|location|loop|recursive)", "", s)
    return s.strip()


def recently_proposed(memory: dict[str, Any], *, title: str, dedup_key: str, now: datetime) -> bool:
    """Return True if this title or dedup_key was proposed within RECENTLY_PROPOSED_WINDOW_SECONDS.

    Also prunes the index of entries older than the window.  Title comparison uses
    _normalise_proposal_title to tolerate scan-drift in metric suffixes.
    """
    cutoff = now.timestamp() - RECENTLY_PROPOSED_WINDOW_SECONDS
    index: dict[str, float] = {}
    for key, raw_ts in memory.get("proposed_index", {}).items():
        try:
            ts = float(raw_ts)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            index[key] = ts
    memory["proposed_index"] = index
    title_key = _normalise_proposal_title(title)
    dedup_key_norm = dedup_key.strip().lower()
    return title_key in index or dedup_key_norm in index


def record_proposed(memory: dict[str, Any], *, title: str, dedup_key: str, now: datetime) -> None:
    """Record a newly created proposal in the index using the normalised title."""
    index = dict(memory.get("proposed_index", {}))
    ts = now.timestamp()
    index[_normalise_proposal_title(title)] = ts
    index[dedup_key.strip().lower()] = ts
    memory["proposed_index"] = index


def handle_propose_cycle(
    client: PlaneClient,
    service: ExecutionService,
    *,
    status_dir: Path | None = None,
    now: datetime | None = None,
) -> ProposalCycleResult:
    now = now or datetime.now(UTC)
    issues = client.list_issues()
    board_idle = board_is_idle_for_proposals_from_issues(issues)

    # When board is idle (no active tasks), promote existing Backlog tasks before
    # creating new ones — work through the queue before adding more.
    if board_idle:
        promoted_ids = promote_backlog_tasks(client, issues)
        if promoted_ids:
            logger = logging.getLogger(__name__)
            logger.info(json.dumps({"event": "propose_backlog_promoted", "task_ids": promoted_ids}))
            return ProposalCycleResult(
                created_task_ids=promoted_ids,
                decision="backlog_promoted",
                board_idle=board_idle,
                reason_summary=f"Promoted {len(promoted_ids)} backlog task(s) to Ready for AI.",
                proposed_state="Ready for AI",
            )

    # Throttle new proposals when the board already has enough active work.
    active_count = active_task_count_from_issues(issues)
    if active_count >= MAX_ACTIVE_TASKS_FOR_PROPOSALS:
        return ProposalCycleResult(
            created_task_ids=[],
            decision="board_congested",
            board_idle=False,
            reason_summary=(
                f"Proposal creation throttled: {active_count} tasks already active "
                f"(limit {MAX_ACTIVE_TASKS_FOR_PROPOSALS})."
            ),
        )

    # Point 8: Satiation — if the last several cycles produced nothing new, the
    # repo is in a stable state.  Stop generating proposals until something changes.
    if service.usage_store.is_proposal_satiated(now=now):
        return ProposalCycleResult(
            created_task_ids=[],
            decision="satiated",
            board_idle=board_idle,
            reason_summary=(
                "Proposal cycle satiated: recent cycles produced no new unique proposals. "
                "The repo appears to be in a stable state — no further proposals until "
                "external changes or board activity create new signal."
            ),
        )

    remaining = service.usage_store.remaining_exec_capacity(now=now)
    min_remaining = service.settings.execution_controls().min_remaining_exec_for_proposals
    if remaining < min_remaining:
        service.usage_store.record_proposal_budget_suppression(
            reason="proposal_budget_too_low",
            now=now,
            evidence={"remaining_exec_capacity": remaining, "min_required": min_remaining},
        )
        return ProposalCycleResult(
            created_task_ids=[],
            decision="proposal_budget_too_low",
            board_idle=board_idle,
            reason_summary=(
                f"Proposal creation suppressed because remaining execution budget is too low "
                f"({remaining} remaining, minimum {min_remaining})."
            ),
        )
    memory = load_proposal_memory(status_dir)
    if proposal_cooldown_active(memory, now):
        return ProposalCycleResult(
            created_task_ids=[],
            decision="cooldown_active",
            board_idle=board_idle,
            reason_summary="Proposal cooldown is still active.",
        )
    if proposal_quota_exhausted(memory, now):
        save_proposal_memory(status_dir, memory)
        return ProposalCycleResult(
            created_task_ids=[],
            decision="quota_exhausted",
            board_idle=board_idle,
            reason_summary="Proposal quota for the current time window has been exhausted.",
        )

    repo_keys = proposal_repo_keys(service)
    if not repo_keys:
        return ProposalCycleResult(
            created_task_ids=[],
            decision="no_repo_enabled",
            board_idle=board_idle,
            reason_summary="No repos are enabled for propose polling.",
        )
    proposals: list[ProposalSpec] = []
    notes: list[str] = []
    classification_counts = recent_classification_counts(client, issues=issues)
    for repo_key in repo_keys:
        try:
            repo_proposals, repo_notes, repo_board_idle = build_proposal_candidates(
                client,
                service,
                repo_key=repo_key,
                issues=issues,
                classification_counts=classification_counts,
            )
        except TypeError:
            repo_proposals, repo_notes, repo_board_idle = build_proposal_candidates(
                client,
                service,
                repo_key=repo_key,
                issues=issues,
            )
        proposals.extend(repo_proposals)
        notes.extend(repo_notes)
        board_idle = board_idle and repo_board_idle
    # Interleave proposals by repo so each enabled repo gets a fair turn within
    # the per-cycle cap (avoids a high-signal repo always crowding out others).
    if len(repo_keys) > 1:
        by_repo: dict[str, list[ProposalSpec]] = {}
        for p in proposals:
            by_repo.setdefault(p.repo_key, []).append(p)
        interleaved: list[ProposalSpec] = []
        buckets = [by_repo[k] for k in repo_keys if k in by_repo]
        while any(buckets):
            for bucket in buckets:
                if bucket:
                    interleaved.append(bucket.pop(0))
        proposals = interleaved
    if not proposals:
        return ProposalCycleResult(
            created_task_ids=[],
            decision="no_proposal",
            board_idle=board_idle,
            reason_summary="No sufficiently grounded autonomous proposal was available.",
        )

    existing_names = existing_issue_names(client, issues=issues)
    proposal_keys = existing_proposal_keys(client, issues=issues)
    created_ids: list[str] = []
    created_states: set[str] = set()
    skipped_conflict = 0
    deduped_count = 0

    # Build a set of open-PR file basenames once for the whole cycle so the
    # per-proposal conflict check doesn't fan out into multiple GitHub API calls.
    open_pr_files = _collect_open_pr_files(service)

    for proposal in proposals:
        if len(created_ids) >= MAX_PROPOSALS_PER_CYCLE:
            break
        # Skip proposals that conflict with an in-flight task touching the same file.
        if _has_conflict_with_active_task(proposal.title, issues, service.usage_store, open_pr_files):
            _logger.info(json.dumps({"event": "propose_conflict_skipped", "title": proposal.title, "open_pr_files_count": len(open_pr_files)}))
            skipped_conflict += 1
            continue
        created = create_proposed_task_if_missing(
            client,
            service,
            proposal=proposal,
            existing_names=existing_names,
            proposal_keys=proposal_keys,
            memory=memory,
            now=now,
        )
        if created is None:
            deduped_count += 1
            continue
        created_ids.append(str(created.get("id")))
        created_states.add(proposal.recommended_state)

    # Point 8: Record cycle outcome for satiation tracking.
    service.usage_store.record_proposal_cycle(
        created=len(created_ids),
        deduped=deduped_count,
        skipped=skipped_conflict,
        now=now,
    )

    if created_ids:
        memory["last_proposal_at"] = now.isoformat()
        timestamps = list(memory.get("proposal_timestamps", []))
        timestamps.extend([now.timestamp()] * len(created_ids))
        memory["proposal_timestamps"] = timestamps
        save_proposal_memory(status_dir, memory)
        proposed_state = "Ready for AI" if created_states == {"Ready for AI"} else "Backlog"
        return ProposalCycleResult(
            created_task_ids=created_ids,
            decision="tasks_created",
            board_idle=board_idle,
            reason_summary="; ".join(notes[:4]) if notes else "Autonomous proposal cycle created bounded tasks.",
            proposed_state=proposed_state,
        )

    save_proposal_memory(status_dir, memory)
    return ProposalCycleResult(
        created_task_ids=[],
        decision="deduped",
        board_idle=board_idle,
        reason_summary="Proposal candidates were already represented by open work.",
    )


def handle_goal_task(client: PlaneClient, service: ExecutionService, task_id: str) -> list[str]:
    issue = client.fetch_issue(task_id)
    selected_evidence = selected_evidence_from_issue(issue)
    target_area_hint = target_area_hint_from_issue(issue)
    result = run_service_task(service, client, task_id, worker_role="goal")
    created_ids: list[str] = []
    if result.outcome_status == "no_op":
        client.comment_issue(
            task_id,
            render_worker_comment(
                "[Goal] No meaningful repo change produced",
                [
                    f"run_id: {result.run_id}",
                    f"task_id: {task_id}",
                    "task_kind: goal",
                    "result_status: blocked",
                    f"outcome_reason: {result.outcome_reason or 'no_op'}",
                    f"selected_evidence: {selected_evidence}",
                    f"target_area_hint: {target_area_hint}",
                    "bounded_scope_reason: execution produced no meaningful repo change to verify",
                    "follow_up_task_ids: none",
                    "next_action: investigate why execution only changed internal executor files",
                ],
            ),
        )
        rewrite_worker_summary(result, service, task_id)
        return created_ids
    if goal_failure_needs_manual_env_fix(result):
        result.blocked_classification = "infra_tooling"
        client.comment_issue(
            task_id,
            render_worker_comment(
                "[Goal] Execution blocked by environment/auth",
                [
                    f"run_id: {result.run_id}",
                    f"task_id: {task_id}",
                    "task_kind: goal",
                    "result_status: blocked",
                    "blocked_classification: infra_tooling",
                    f"selected_evidence: {selected_evidence}",
                    f"target_area_hint: {target_area_hint}",
                    "bounded_scope_reason: task remained scoped to one concrete evidence-led target but execution tooling failed first",
                    f"detail: {result.execution_stderr_excerpt or 'provider or backend configuration failed before execution'}",
                    "follow_up_task_ids: none",
                    "next_action: fix the configured Kodo orchestrator or provider auth before retrying",
                ],
            ),
        )
        rewrite_worker_summary(result, service, task_id)
        return created_ids

    if result.success:
        if goal_requires_test_follow_up(issue, result):
            existing_names = existing_issue_names(client)
            existing_follow_ups = existing_follow_up_keys(client)
            follow_up = create_follow_up_task_if_missing(
                client,
                service,
                existing_names=existing_names,
                existing_follow_ups=existing_follow_ups,
                source_role="goal",
                task_kind="test",
                original_issue=issue,
                title=f"Verify {issue.get('name', 'goal task')}",
                goal_text=(
                    f"Verify the implementation from '{issue.get('name', 'Untitled')}' and confirm the change behaves correctly without regressions."
                ),
                handoff_reason="goal_completed_requires_verification",
                constraints_text=(
                    f"- source_task_id: {task_id}\n"
                    f"- source_run_id: {result.run_id}\n"
                    "- focus on validating the implemented behavior and likely regressions"
                ),
            )
            if follow_up is not None:
                created_ids.append(str(follow_up.get("id")))
            result.follow_up_task_ids = created_ids
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Goal] Execution complete; handed off to Test",
                    [
                        f"run_id: {result.run_id}",
                        f"task_id: {task_id}",
                        "task_kind: goal",
                        "result_status: review",
                        f"selected_evidence: {selected_evidence}",
                        f"target_area_hint: {target_area_hint}",
                        "bounded_scope_reason: follow-up verification is scoped to the single evidence-led change from this goal run",
                        f"follow_up_task_ids: {', '.join(created_ids) if created_ids else 'none'}",
                        "handoff_reason: goal_completed_requires_verification",
                    ],
                ),
            )
        else:
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Goal] Execution complete; no explicit Test handoff",
                    [
                        f"run_id: {result.run_id}",
                        f"task_id: {task_id}",
                        "task_kind: goal",
                        "result_status: review",
                        f"selected_evidence: {selected_evidence}",
                        f"target_area_hint: {target_area_hint}",
                        "bounded_scope_reason: the run stayed within one evidence-led target and did not require separate verification handoff",
                        "follow_up_task_ids: none",
                        "handoff_reason: goal_completed_no_explicit_test_required",
                    ],
                ),
            )
    else:
        result.blocked_classification = classify_execution_result(result)
        client.comment_issue(
            task_id,
            render_worker_comment(
                "[Goal] Blocked; handed off to Improve triage",
                [
                    f"run_id: {result.run_id}",
                    f"task_id: {task_id}",
                    "task_kind: goal",
                    "result_status: blocked",
                    f"blocked_classification: {result.blocked_classification}",
                    f"selected_evidence: {selected_evidence}",
                    f"target_area_hint: {target_area_hint}",
                    "bounded_scope_reason: the run attempted one evidence-led change and blocked before completion",
                    "follow_up_task_ids: none",
                    "handoff_reason: goal_blocked_requires_improve_triage",
                ],
            ),
        )
    rewrite_worker_summary(result, service, task_id)
    return created_ids


def goal_failure_needs_manual_env_fix(result: ExecutionResult) -> bool:
    excerpt = (result.execution_stderr_excerpt or "").lower()
    if not excerpt:
        return False
    return any(
        token in excerpt
        for token in [
            "anthropic_api_key not set",
            "api key not set",
            "login required",
            "authentication",
            "auth",
        ]
    )


def handle_test_task(client: PlaneClient, service: ExecutionService, task_id: str) -> list[str]:
    issue = client.fetch_issue(task_id)
    selected_evidence = selected_evidence_from_issue(issue)
    target_area_hint = target_area_hint_from_issue(issue)
    result = run_service_task(service, client, task_id, worker_role="test")
    if result.outcome_status == "no_op":
        result.final_status = "Blocked"
        client.comment_issue(
            task_id,
            render_worker_comment(
                "[Test] Verification produced no meaningful repo change",
                [
                    f"run_id: {result.run_id}",
                    f"task_id: {task_id}",
                    "task_kind: test",
                    "result_status: blocked",
                    f"outcome_reason: {result.outcome_reason or 'no_op'}",
                    f"selected_evidence: {selected_evidence}",
                    f"target_area_hint: {target_area_hint}",
                    "bounded_scope_reason: verification found no meaningful repo change tied to the selected evidence",
                    "follow_up_task_ids: none",
                    "next_action: investigate why verification only touched internal executor files",
                ],
            ),
        )
        rewrite_worker_summary(result, service, task_id)
        return []
    if result.success:
        client.transition_issue(task_id, "Done")
        result.final_status = "Done"
        client.comment_issue(
            task_id,
            render_worker_comment(
                "[Test] Verification completed",
                [
                    f"run_id: {result.run_id}",
                    f"task_id: {task_id}",
                    "task_kind: test",
                    "result_status: done",
                    f"validation_passed: {result.validation_passed}",
                    f"selected_evidence: {selected_evidence}",
                    f"target_area_hint: {target_area_hint}",
                    "bounded_scope_reason: verification stayed scoped to the same evidence-led target area",
                    "follow_up_task_ids: none",
                    "handoff_reason: verification_passed",
                ],
            ),
        )
        rewrite_worker_summary(result, service, task_id)
        return []

    blocked_classification = classify_execution_result(result)
    follow_up = create_follow_up_task(
        client,
        service,
        source_role="test",
        task_kind="goal",
        original_issue=issue,
        title=f"Fix regression from {issue.get('name', 'test task')}",
        goal_text=(
            f"Investigate the failed test task '{issue.get('name', 'Untitled')}' and implement the changes needed "
            "to make the verification pass."
        ),
        handoff_reason="test_failed_requires_goal_follow_up",
        constraints_text=(
            f"- source_task_id: {task_id}\n"
            f"- source_run_id: {result.run_id}\n"
            f"- blocked_classification: {blocked_classification}\n"
            "- keep the fix scoped to the failure described in the test task"
        ),
    )
    result.follow_up_task_ids = [str(follow_up.get("id"))]
    result.final_status = "Blocked"
    result.blocked_classification = blocked_classification
    client.comment_issue(
        task_id,
        render_worker_comment(
            "[Test] Verification failed; follow-up goal task created",
            [
                f"run_id: {result.run_id}",
                f"task_id: {task_id}",
                "task_kind: test",
                "result_status: blocked",
                f"validation_passed: {result.validation_passed}",
                f"blocked_classification: {blocked_classification}",
                f"selected_evidence: {selected_evidence}",
                f"target_area_hint: {target_area_hint}",
                "bounded_scope_reason: verification failure stayed scoped to the same evidence-led target and generated one bounded fix task",
                f"follow_up_task_ids: {', '.join(result.follow_up_task_ids)}",
                "handoff_reason: test_failed_requires_goal_follow_up",
            ],
        ),
    )
    rewrite_worker_summary(result, service, task_id)
    return result.follow_up_task_ids


def handle_improve_task(client: PlaneClient, service: ExecutionService, task_id: str) -> list[str]:
    issue = client.fetch_issue(task_id)
    existing_names = existing_issue_names(client)
    existing_follow_ups = existing_follow_up_keys(client)
    repo_key, base_branch, _allowed_paths = issue_execution_target(issue, service)
    findings, report_notes = discover_improvement_candidates(service, repo_key=repo_key, base_branch=base_branch)
    created_ids: list[str] = []
    created_titles: list[str] = []

    for finding in findings:
        if len(created_ids) >= MAX_IMPROVE_FOLLOW_UPS_PER_CYCLE:
            break
        created = create_follow_up_task_if_missing(
            client,
            service,
            existing_names=existing_names,
            existing_follow_ups=existing_follow_ups,
            source_role="improve",
            task_kind=finding["kind"],
            original_issue=issue,
            title=finding["title"],
            goal_text=finding["goal"],
            handoff_reason=f"improve_discovery_{finding['kind']}",
            constraints_text=(
                f"- source_task_id: {task_id}\n"
                f"{finding['constraints']}"
            ),
        )
        if created is not None:
            created_ids.append(str(created.get("id")))
            created_titles.append(str(created.get("name")))

    comment_lines = [
        f"task_id: {task_id}",
        "task_kind: improve",
        f"result_status: {'review' if created_ids else 'done'}",
        f"follow_up_task_ids: {', '.join(created_ids) if created_ids else 'none'}",
        *report_notes,
    ]
    if findings:
        comment_lines.append("discovered_findings:")
        for finding in findings:
            comment_lines.append(f"{finding['title']}")
            comment_lines.append(finding["note"].lstrip("- ").strip())

    client.comment_issue(task_id, render_worker_comment(IMPROVE_COMMENT_MARKER, comment_lines))
    client.transition_issue(task_id, "Review" if created_ids else "Done")
    return created_ids


def handle_fix_pr_task(client: PlaneClient, service: ExecutionService, task_id: str) -> None:
    """Dispatch a fix_pr task to ExecutionService and log the outcome."""
    result = service.run_fix_pr_task(client, task_id)
    _logger.info(json.dumps({
        "event": "fix_pr_complete",
        "task_id": task_id,
        "outcome_status": result.outcome_status,
        "changed_files": len(result.changed_files),
        "success": result.success,
    }))


def handle_blocked_triage(client: PlaneClient, service: ExecutionService, task_id: str) -> tuple[str, list[str]]:
    issue = client.fetch_issue(task_id)
    comments = client.list_comments(task_id)
    triage = build_improve_triage_result(client, issue, comments)
    created_ids: list[str] = []

    existing_names = existing_issue_names(client)
    existing_follow_ups = existing_follow_up_keys(client)

    if triage.follow_up is not None and not issue_is_unblock_chain(issue):
        created = create_follow_up_task_if_missing(
            client,
            service,
            existing_names=existing_names,
            existing_follow_ups=existing_follow_ups,
            source_role="improve",
            task_kind=triage.follow_up.task_kind,
            original_issue=issue,
            title=triage.follow_up.title,
            goal_text=triage.follow_up.goal_text,
            handoff_reason=triage.follow_up.handoff_reason,
            constraints_text=triage.follow_up.constraints_text,
        )
        if created is not None:
            created_ids.append(str(created.get("id")))
    elif issue_is_unblock_chain(issue):
        triage.reason_summary = (
            f"{triage.reason_summary} Improve already generated this unblock task, so the watcher will not create another "
            "recursive unblock follow-up."
        )

    client.comment_issue(
        task_id,
        render_worker_comment(
            TRIAGE_COMMENT_MARKER,
            [
                f"task_id: {task_id}",
                f"task_kind: {task_kind_for_issue(issue)}",
                "result_status: blocked",
                f"blocked_classification: {triage.classification}",
                f"certainty: {triage.certainty}",
                f"reason: {triage.reason_summary}",
                f"follow_up_task_ids: {', '.join(created_ids) if created_ids else 'none'}",
                f"next_action: {triage.recommended_action if not created_ids else 'follow_up_created'}",
                f"human_attention_required: {str(triage.human_attention_required).lower()}",
                f"handoff_reason: {triage.follow_up.handoff_reason if triage.follow_up and created_ids else ('improve_triage_human_attention' if triage.human_attention_required else 'improve_triage_no_change')}",
            ],
        ),
    )
    client.transition_issue(task_id, "Blocked")
    if triage.human_attention_required:
        notify_human_attention(
            task_id=task_id,
            task_title=str(issue.get("name", "")),
            classification=triage.classification,
            reason=triage.reason_summary,
        )
    return triage.classification, created_ids


def run_watch_loop(
    client: PlaneClient,
    service: ExecutionService,
    *,
    role: str,
    ready_state: str,
    poll_interval_seconds: int,
    max_cycles: int | None,
    status_dir: Path | None = None,
) -> None:
    logger = logging.getLogger(__name__)
    poll_interval_seconds = max(poll_interval_seconds, service.settings.execution_controls().min_watch_interval_seconds)
    cycle = 0
    known_triaged_blocked_ids: set[str] = set()
    counters = {"follow_up_tasks_created": 0, "blocked_tasks_triaged": 0}

    while True:
        cycle += 1
        cycle_run_id = f"{role}-cycle-{cycle}"
        logger.info(json.dumps({"event": "watch_cycle_start", "role": role, "cycle": cycle, "poll_interval_seconds": poll_interval_seconds, "run_id": cycle_run_id}))
        write_watch_status(
            status_dir=status_dir,
            role=role,
            cycle=cycle,
            state="polling",
            run_id=cycle_run_id,
            last_action="cycle_start",
            counters=counters,
        )
        try:
            if cycle == 1:
                reconciled_ids = reconcile_stale_running_issues(client, role=role, ready_state=ready_state)
                if reconciled_ids:
                    logger.info(json.dumps({"event": "watch_reconciled_stale_running", "role": role, "task_ids": reconciled_ids}))
            # Point 4+6: Post-merge CI regression scan — runs every 10 cycles for the
            # improve watcher so CI failures on merged PRs create follow-up tasks.
            if role == "improve" and cycle % 10 == 0:
                reg_ids = detect_post_merge_regressions(client, service)
                if reg_ids:
                    counters["follow_up_tasks_created"] += len(reg_ids)
                    logger.info(json.dumps({"event": "watch_post_merge_regressions", "role": role, "cycle": cycle, "regression_task_ids": reg_ids}))
            task_id, action = select_watch_candidate(
                client,
                ready_state=ready_state,
                role=role,
                known_triaged_blocked_ids=known_triaged_blocked_ids,
                service=service,
            )
            logger.info(json.dumps({"event": "watch_task_selected", "role": role, "cycle": cycle, "task_id": task_id, "action": action, "run_id": cycle_run_id}))
            issue = client.fetch_issue(task_id)
            status_name = issue_status_name(issue)
            task_kind = issue_task_kind(issue)
            eligible = False
            if action == "execute":
                eligible = status_name == ready_state and task_kind == role
            elif action == "improve_task":
                eligible = status_name == ready_state and task_kind == "improve"
            elif action in ("blocked_triage", "blocked_resolution_complete", "blocked_pr_merged"):
                eligible = status_name == "Blocked"

            if not eligible:
                logger.info(json.dumps(
                    {
                        "event": "watch_task_skipped",
                        "role": role,
                        "cycle": cycle,
                        "task_id": task_id,
                        "reason": "state_or_kind_changed",
                        "task_kind": task_kind,
                        "status": status_name,
                        "run_id": cycle_run_id,
                    }
                ))
                write_watch_status(
                    status_dir=status_dir,
                    role=role,
                    cycle=cycle,
                    state="idle",
                    run_id=cycle_run_id,
                    last_action="task_skipped",
                    task_id=task_id,
                    task_kind=task_kind,
                    counters=counters,
                )
            else:
                gate = execution_gate_decision(service=service, role=role, action=action, issue=issue)
                if gate is not None:
                    gate_action, evidence = gate
                    logger.info(
                        json.dumps(
                            {
                                "event": gate_action,
                                "role": role,
                                "cycle": cycle,
                                "task_id": task_id,
                                "task_kind": task_kind,
                                "action": action,
                                "evidence": evidence,
                                "run_id": cycle_run_id,
                            }
                        )
                    )
                    if gate_action == "retry_cap_block":
                        client.transition_issue(task_id, "Blocked")
                        client.comment_issue(
                            task_id,
                            render_worker_comment(
                                f"[{worker_title(role)}] Execution blocked by retry cap",
                                [
                                    f"task_id: {task_id}",
                                    f"task_kind: {task_kind}",
                                    "result_status: blocked",
                                    f"reason: {evidence.get('reason')}",
                                    f"attempts: {evidence.get('attempts')}",
                                    f"limit: {evidence.get('limit')}",
                                ],
                            ),
                        )
                    write_watch_status(
                        status_dir=status_dir,
                        role=role,
                        cycle=cycle,
                        state="idle",
                        run_id=cycle_run_id,
                        last_action=gate_action,
                        task_id=task_id,
                        task_kind=task_kind,
                        counters=counters,
                    )
                    if max_cycles is not None and cycle >= max_cycles:
                        logger.info(json.dumps({"event": "watch_complete", "role": role, "cycles": cycle, "run_id": cycle_run_id}))
                        return
                    logger.info(json.dumps({"event": "watch_cycle_end", "role": role, "cycle": cycle, "sleep_interval_seconds": poll_interval_seconds, "run_id": cycle_run_id}))
                    time.sleep(poll_interval_seconds)
                    continue
                claimed = False
                try:
                    client.transition_issue(task_id, "Running")
                    claimed = True
                    logger.info(json.dumps(
                        {
                            "event": "watch_task_claimed",
                            "role": role,
                            "cycle": cycle,
                            "task_id": task_id,
                            "task_kind": task_kind,
                            "action": action,
                            "run_id": cycle_run_id,
                        }
                    ))
                    write_watch_status(
                        status_dir=status_dir,
                        role=role,
                        cycle=cycle,
                        state="active",
                        run_id=cycle_run_id,
                        last_action="task_claimed",
                        task_id=task_id,
                        task_kind=task_kind,
                        counters=counters,
                    )
                    if role == "goal":
                        created_ids = handle_goal_task(client, service, task_id)
                        counters["follow_up_tasks_created"] += len(created_ids)
                        logger.info(json.dumps({"event": "watch_action_complete", "role": role, "cycle": cycle, "task_id": task_id, "task_kind": task_kind, "action": "execute", "created_task_ids": created_ids, "run_id": cycle_run_id}))
                        write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="execute_complete", task_id=task_id, task_kind=task_kind, follow_up_task_ids=created_ids, counters=counters)
                    elif role == "test":
                        created_ids = handle_test_task(client, service, task_id)
                        counters["follow_up_tasks_created"] += len(created_ids)
                        logger.info(json.dumps({"event": "watch_action_complete", "role": role, "cycle": cycle, "task_id": task_id, "task_kind": task_kind, "action": "execute", "created_task_ids": created_ids, "run_id": cycle_run_id}))
                        write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="execute_complete", task_id=task_id, task_kind=task_kind, follow_up_task_ids=created_ids, counters=counters)
                    elif role == "improve":
                        if action == "blocked_triage":
                            classification, created_ids = handle_blocked_triage(client, service, task_id)
                            known_triaged_blocked_ids.add(task_id)
                            counters["follow_up_tasks_created"] += len(created_ids)
                            counters["blocked_tasks_triaged"] += 1
                            logger.info(json.dumps({"event": "watch_triage_complete", "role": role, "cycle": cycle, "task_id": task_id, "task_kind": task_kind, "classification": classification, "created_task_ids": created_ids, "run_id": cycle_run_id}))
                            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="blocked_triage_complete", task_id=task_id, task_kind=task_kind, follow_up_task_ids=created_ids, blocked_classification=classification, counters=counters)
                        elif action == "blocked_resolution_complete":
                            follow_up_ids = extract_triage_follow_up_ids(client, task_id)
                            client.transition_issue(task_id, ready_state)
                            client.comment_issue(
                                task_id,
                                render_worker_comment(
                                    f"{UNBLOCK_COMMENT_MARKER} — task unblocked",
                                    [
                                        f"task_id: {task_id}",
                                        f"task_kind: {task_kind}",
                                        f"result_status: {ready_state.lower().replace(' ', '_')}",
                                        f"resolved_by: {', '.join(follow_up_ids)}",
                                        "reason: all resolution follow-up tasks completed",
                                    ],
                                ),
                            )
                            known_triaged_blocked_ids.discard(task_id)
                            logger.info(json.dumps({"event": "watch_unblocked", "role": role, "cycle": cycle, "task_id": task_id, "task_kind": task_kind, "resolved_by": follow_up_ids, "run_id": cycle_run_id}))
                            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="blocked_resolution_complete", task_id=task_id, task_kind=task_kind, counters=counters)
                        elif action == "blocked_pr_merged":
                            # PR for this task was merged — implementation succeeded; close the task.
                            artifact = UsageStore().get_task_artifact(task_id)
                            pr_url = str((artifact or {}).get("pull_request_url") or "")
                            issue_for_pr = client.fetch_issue(task_id)
                            client.transition_issue(task_id, "Done")
                            client.comment_issue(
                                task_id,
                                render_worker_comment(
                                    "[Improve] Blocked task auto-closed: PR already merged",
                                    [
                                        f"task_id: {task_id}",
                                        f"task_kind: {task_kind}",
                                        "result_status: done",
                                        f"pull_request_url: {pr_url}",
                                        "reason: the PR created by the previous execution was merged — task is complete",
                                    ],
                                ),
                            )
                            known_triaged_blocked_ids.add(task_id)
                            logger.info(json.dumps({"event": "watch_pr_merged_autoclosed", "role": role, "cycle": cycle, "task_id": task_id, "pr_url": pr_url, "run_id": cycle_run_id}))
                            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="blocked_pr_merged", task_id=task_id, task_kind=task_kind, counters=counters)
                        elif action == "blocked_stale_escalation":
                            # Task has been blocked with human_attention for >7 days with no follow-up.
                            # Create a re-triage goal task and mark this one as escalated.
                            issue_for_escalation = client.fetch_issue(task_id)
                            issue_title = str(issue_for_escalation.get("name", "blocked task"))
                            escalation_task = client.create_issue(
                                name=f"Re-triage: {issue_title}",
                                description=(
                                    f"## Execution\nrepo: {_extract_repo_key(issue_for_escalation, service)}\nmode: goal\n\n"
                                    f"## Goal\n"
                                    f"This task was blocked with human attention required for over {STALE_BLOCKED_ESCALATION_DAYS} days "
                                    f"without resolution. Re-examine the original blocked task '{issue_title}' ({task_id}) and either:\n"
                                    "- Resolve the underlying issue and re-queue the original task, or\n"
                                    "- Close the original task if it is no longer relevant.\n\n"
                                    "## Context\n"
                                    f"- original_task_id: {task_id}\n"
                                    f"- original_task_kind: {task_kind}\n"
                                    "- escalation_reason: stale_blocked_human_attention\n"
                                ),
                                state="Ready for AI",
                                label_names=["task-kind: goal", "source: improve-worker"],
                            )
                            escalation_id = str(escalation_task.get("id", ""))
                            client.transition_issue(task_id, "Blocked")
                            client.comment_issue(
                                task_id,
                                render_worker_comment(
                                    STALE_ESCALATION_MARKER,
                                    [
                                        f"task_id: {task_id}",
                                        f"task_kind: {task_kind}",
                                        "result_status: blocked",
                                        f"escalation_task_id: {escalation_id}",
                                        f"reason: blocked with human_attention_required for >{STALE_BLOCKED_ESCALATION_DAYS} days",
                                    ],
                                ),
                            )
                            known_triaged_blocked_ids.add(task_id)
                            counters["follow_up_tasks_created"] += 1
                            logger.info(json.dumps({"event": "watch_stale_escalation", "role": role, "cycle": cycle, "task_id": task_id, "escalation_task_id": escalation_id, "run_id": cycle_run_id}))
                            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="blocked_stale_escalation", task_id=task_id, task_kind=task_kind, follow_up_task_ids=[escalation_id], counters=counters)
                        elif action == "fix_pr_task":
                            handle_fix_pr_task(client, service, task_id)
                            logger.info(json.dumps({"event": "watch_fix_pr_complete", "role": role, "cycle": cycle, "task_id": task_id, "task_kind": task_kind, "run_id": cycle_run_id}))
                            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="fix_pr_complete", task_id=task_id, task_kind=task_kind, counters=counters)
                        else:
                            created_ids = handle_improve_task(client, service, task_id)
                            counters["follow_up_tasks_created"] += len(created_ids)
                            logger.info(json.dumps({"event": "watch_improve_complete", "role": role, "cycle": cycle, "task_id": task_id, "task_kind": task_kind, "created_task_ids": created_ids, "run_id": cycle_run_id}))
                            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="improve_complete", task_id=task_id, task_kind=task_kind, follow_up_task_ids=created_ids, counters=counters)
                    elif role == "propose":
                        raise ValueError("Propose role is handled outside task claiming")
                    else:
                        raise ValueError(f"Unsupported worker role '{role}'")
                except Exception:
                    if claimed:
                        _blocked_actions = {"blocked_triage", "blocked_resolution_complete", "blocked_stale_escalation", "blocked_pr_merged"}
                        _error_state = "Blocked" if action in _blocked_actions else ready_state
                        client.transition_issue(task_id, _error_state)
                        client.comment_issue(
                            task_id,
                            render_worker_comment(
                                f"[{worker_title(role)}] Task returned to queue after worker error",
                                [
                                    f"task_id: {task_id}",
                                    f"task_kind: {task_kind}",
                                    f"action: {action}",
                                    f"result_status: {_error_state.lower().replace(' ', '_')}",
                                    "reason: worker raised after claiming the task",
                                ],
                            ),
                        )
                    raise
        except ValueError as exc:
            if role == "propose":
                proposal_result = handle_propose_cycle(client, service, status_dir=status_dir)
                counters["follow_up_tasks_created"] += len(proposal_result.created_task_ids)
                event_name = "watch_propose_complete" if proposal_result.created_task_ids else "watch_propose_noop"
                logger.info(
                    json.dumps(
                        {
                            "event": event_name,
                            "role": role,
                            "cycle": cycle,
                            "decision": proposal_result.decision,
                            "board_idle": proposal_result.board_idle,
                            "created_task_ids": proposal_result.created_task_ids,
                            "reason_summary": proposal_result.reason_summary,
                            "run_id": cycle_run_id,
                        }
                    )
                )
                write_watch_status(
                    status_dir=status_dir,
                    role=role,
                    cycle=cycle,
                    state="idle",
                    run_id=cycle_run_id,
                    last_action=proposal_result.decision,
                    task_kind="propose",
                    follow_up_task_ids=proposal_result.created_task_ids,
                    counters=counters,
                )
            else:
                logger.info(json.dumps({"event": "watch_no_task", "role": role, "cycle": cycle, "message": str(exc).replace('"', "'"), "run_id": cycle_run_id}))
                write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="idle", run_id=cycle_run_id, last_action="no_task", counters=counters)
        except httpx.HTTPStatusError as exc:
            response = exc.response
            if response.status_code == 429:
                retry_after_header = response.headers.get("Retry-After", "").strip()
                retry_after = int(retry_after_header) if retry_after_header.isdigit() else 0
                backoff_seconds = max(poll_interval_seconds * RATE_LIMIT_BACKOFF_MULTIPLIER, retry_after)
                logger.info(json.dumps({"event": "watch_rate_limited", "role": role, "cycle": cycle, "status": 429, "backoff_seconds": backoff_seconds, "run_id": cycle_run_id}))
                write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="backoff", run_id=cycle_run_id, last_action="rate_limited", counters=counters)
                if max_cycles is not None and cycle >= max_cycles:
                    logger.info(json.dumps({"event": "watch_complete", "role": role, "cycles": cycle, "run_id": cycle_run_id}))
                    return
                time.sleep(backoff_seconds)
                continue
            logger.info(json.dumps({"event": "watch_error", "role": role, "cycle": cycle, "message": str(exc).replace('"', "'"), "run_id": cycle_run_id}))
            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="error", run_id=cycle_run_id, last_action="watch_error", counters=counters)
        except Exception as exc:
            logger.info(json.dumps({"event": "watch_error", "role": role, "cycle": cycle, "message": str(exc).replace('"', "'"), "run_id": cycle_run_id}))
            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="error", run_id=cycle_run_id, last_action="watch_error", counters=counters)

        if max_cycles is not None and cycle >= max_cycles:
            logger.info(json.dumps({"event": "watch_complete", "role": role, "cycles": cycle, "run_id": cycle_run_id}))
            return
        logger.info(json.dumps({"event": "watch_cycle_end", "role": role, "cycle": cycle, "sleep_interval_seconds": poll_interval_seconds, "run_id": cycle_run_id}))
        time.sleep(poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Plane task(s) through Kodo wrapper")
    parser.add_argument("--config", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--task-id")
    target.add_argument("--first-ready", action="store_true")
    target.add_argument("--watch", action="store_true")
    parser.add_argument("--ready-state", default="Ready for AI")
    parser.add_argument("--role", default="goal", choices=["goal", "test", "improve", "propose"])
    parser.add_argument("--poll-interval-seconds", type=int, default=15)
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

    try:
        service = ExecutionService(settings)
        if args.watch:
            run_watch_loop(
                client,
                service,
                role=args.role,
                ready_state=args.ready_state,
                poll_interval_seconds=args.poll_interval_seconds,
                max_cycles=args.max_cycles,
                status_dir=Path(args.status_dir) if args.status_dir else None,
            )
            return

        task_id = args.task_id or select_ready_task_id(client, ready_state=args.ready_state, role=args.role)
        if args.first_ready:
            logging.getLogger(__name__).info(
                '{"event":"task_selected","mode":"first_ready","role":"%s","task_id":"%s"}',
                args.role,
                task_id,
            )
        result = service.run_task(client, task_id)
        print(result.model_dump_json(indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
