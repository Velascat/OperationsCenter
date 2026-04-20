from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx

from control_plane.adapters.escalation import post_escalation
from control_plane.adapters.github_pr import GitHubPRClient
from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService, TaskParser
from control_plane.config import load_settings
from control_plane.domain import ExecutionResult
from control_plane.execution.usage_store import UsageStore
from control_plane.proposer.rejection_store import ProposalRejectionStore

# Maps each watcher role to the set of task-kind label values it will claim.
# test_campaign and improve_campaign are spec-director campaign tasks that use
# kodo --test and kodo --improve respectively.
ROLE_TASK_KINDS: dict[str, set[str]] = {
    "goal": {"goal"},
    "test": {"test", "test_campaign"},
    "improve": {"improve", "improve_campaign"},
    "fix_pr": {"fix_pr"},
}

# ---------------------------------------------------------------------------
# S6-8: Module-level kodo version cache (refreshed per watcher startup).
# ---------------------------------------------------------------------------
_kodo_version_cache: dict[str, str | None] = {}


def _get_kodo_version(binary: str) -> str | None:
    """Return the cached kodo binary version string, fetching it on first call."""
    if binary not in _kodo_version_cache:
        from control_plane.adapters.kodo.adapter import KodoAdapter
        _kodo_version_cache[binary] = KodoAdapter.get_version(binary)
    return _kodo_version_cache[binary]


# ---------------------------------------------------------------------------
# S6-1: Maintenance window helpers.
# ---------------------------------------------------------------------------

def _in_maintenance_window(settings: "Any", now: "datetime") -> bool:
    """Return True if *now* falls within any configured maintenance window.

    Windows use UTC hours.  Wrap-midnight windows (start_hour > end_hour) are
    supported.  An empty ``days`` list means the window applies every day.
    """
    windows = getattr(settings, "maintenance_windows", None) or []
    for w in windows:
        days = list(getattr(w, "days", []) or [])
        if days and now.weekday() not in days:
            continue
        start = int(getattr(w, "start_hour", 0))
        end = int(getattr(w, "end_hour", 0))
        hour = now.hour
        if start < end:
            if start <= hour < end:
                return True
        else:  # wraps midnight
            if hour >= start or hour < end:
                return True
    return False


_REJECTION_PATTERNS_PATH = Path("state/rejection_patterns.json")


def _load_rejection_patterns_for_proposal(*, family: str, repo_key: str) -> list[str]:
    """Return the top-3 most common rejection patterns for (repo_key, family).

    Reads the same ``state/rejection_patterns.json`` store that the reviewer
    watcher maintains when it observes human PR rejections.  Returns [] when the
    file is absent or the key has no data.
    """
    try:
        data = json.loads(_REJECTION_PATTERNS_PATH.read_text())
        key = f"{repo_key}:{family}" if (repo_key and family) else (family or repo_key or "unknown")
        entry = data.get(key, {})
        by_count = sorted(entry.get("patterns", {}).items(), key=lambda kv: kv[1], reverse=True)
        return [p for p, _ in by_count[:3]]
    except Exception:
        return []


TRIAGE_COMMENT_MARKER = "[Improve] Blocked triage"
UNBLOCK_COMMENT_MARKER = "[Improve] Resolution complete"
IMPROVE_COMMENT_MARKER = "[Improve] Improvement pass"
PROPOSE_COMMENT_MARKER = "[Propose] Autonomous task created"
# Set CONTROL_PLANE_NOTIFY_WEBHOOK to a URL to receive POST notifications when a
# task is blocked and requires human attention.
_NOTIFY_WEBHOOK_ENV = "CONTROL_PLANE_NOTIFY_WEBHOOK"
RATE_LIMIT_BACKOFF_MULTIPLIER = 4
# Backoff applied when the kodo orchestrator reports a usage/rate-limit.
# The watcher thread sleeps this long before returning so the next poll cycle
# does not immediately re-run kodo while the limit is still active.
ORCHESTRATOR_RATE_LIMIT_BACKOFF_SECONDS = 1800  # 30 minutes
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
MAX_ACTIVE_TASKS_FOR_PROPOSALS = 5
# Don't create new autonomy proposals if this many source:autonomy tasks are already
# queued in Ready for AI or Backlog.  Prevents the propose lane from flooding the board
# faster than the goal/test lanes can drain it.
MAX_QUEUED_AUTONOMY_TASKS = int(os.environ.get("CONTROL_PLANE_MAX_QUEUED_AUTONOMY_TASKS", "15"))
# Refresh the observe→insights→decide pipeline when decision artifacts are older than this.
# Applies only when the board is fully empty (no active or backlog tasks).
_AUTONOMY_ARTIFACT_STALE_HOURS = int(os.environ.get("CONTROL_PLANE_AUTONOMY_STALE_HOURS", "8"))
# Maximum tasks to promote from Backlog → Ready for AI when the board is idle.
MAX_BACKLOG_PROMOTIONS_PER_CYCLE = 2
# Blocked tasks with human_attention_required that have sat untouched longer than this
# will be escalated with a fresh re-triage task.
STALE_BLOCKED_ESCALATION_DAYS = 7
EXECUTION_ACTIONS = {"execute", "improve_task"}
MAX_CLASSIFICATION_ISSUES = 20
# S7-4: Number of consecutive blocks on the same task before self-healing fires.
CONSECUTIVE_BLOCK_COOLDOWN_THRESHOLD = 3
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
                "summary": result.summary or "",
            },
            now=datetime.now(UTC),
        )
    except Exception:
        pass


def _semantic_title_similarity(a: str, b: str) -> float:
    """Jaccard similarity on word tokens between two title strings.

    Returns a value in [0, 1].  Values ≥ 0.5 indicate titles that are likely
    about the same problem and should be treated as near-duplicates for the
    purpose of proposal deduplication.

    This catches cases where exact title dedup misses because the wording
    changed slightly (e.g. "Fix lint errors in api.py" vs "Lint fix: api.py").
    """
    # Strip common prefix markers like [Step 1/3:], [Goal], etc.
    _STRIP_RE = re.compile(r"^\[[^\]]*\]\s*")
    a_clean = _STRIP_RE.sub("", a.lower())
    b_clean = _STRIP_RE.sub("", b.lower())
    tokens_a = set(re.findall(r"\b\w{3,}\b", a_clean))  # words ≥ 3 chars
    tokens_b = set(re.findall(r"\b\w{3,}\b", b_clean))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


_SEMANTIC_DEDUP_THRESHOLD = 0.5


def _extract_filename_tokens(title: str) -> set[str]:
    """Extract *.py filename tokens from a task title for conflict detection."""
    return {m.group(0).lower() for m in re.finditer(r"\b\w[\w.]*\.py\b", title)}


def _extract_evidence_file_tokens(evidence_lines: list[str]) -> set[str]:
    """Extract file path basenames from proposal evidence lines.

    Evidence lines often contain patterns like:
      - file: src/control_plane/foo.py
      - Top uncovered files: foo.py, bar.py
    This gives higher-fidelity conflict data than title tokens alone.
    """
    tokens: set[str] = set()
    for line in evidence_lines:
        # Match any file-like path (any extension, or no extension but looks like a module path)
        for m in re.finditer(r"[\w/.-]+\.\w{1,6}", line):
            tokens.add(Path(m.group(0)).name.lower())
    return tokens


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
        "flaky test": "flaky_test",
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


_CP_QUESTION_RE = re.compile(r"<!--\s*cp:question:(.+?)-->", re.DOTALL | re.IGNORECASE)


def extract_cp_question(result: "ExecutionResult") -> str | None:
    """Extract the question text from a ``<!-- cp:question: ... -->`` marker.

    Checks both the summary and the execution_stderr_excerpt fields since the
    question marker may appear in either depending on the kodo adapter.
    Returns the stripped question text, or None when no marker is present.
    """
    for source in (
        getattr(result, "execution_stdout", None) or "",
        result.execution_stderr_excerpt or "",
        result.summary or "",
    ):
        m = _CP_QUESTION_RE.search(source)
        if m:
            return m.group(1).strip()
    return None


def classify_execution_result(
    result: ExecutionResult,
    usage_store: "UsageStore | None" = None,
) -> str:
    if result.policy_violations:
        return "scope_policy"
    # S10-2: Detect question marker in any output field — Kodo signalled it
    # needs human input before it can proceed.
    if extract_cp_question(result) is not None:
        return "awaiting_input"
    excerpt = (result.execution_stderr_excerpt or "").lower()
    # S7-3: Out-of-memory — check early, before infra_tooling catches "killed"
    if any(
        token in excerpt
        for token in [
            "out of memory",
            "cannot allocate memory",
            "killed",
            "oom",
            "memory allocation failed",
        ]
    ):
        return "oom"
    # S7-3: Hard timeout — kodo process killed by timeout wrapper (exit 124) or
    # subprocess timed out.  Separate from infra auth failures.
    if any(
        token in excerpt
        for token in [
            "timed out",
            "timeout",
            "operation timed out",
            "deadline exceeded",
        ]
    ):
        return "timeout"
    # S7-3: Model API / provider errors (5xx, overloaded, etc.)
    if any(
        token in excerpt
        for token in [
            "internal server error",
            "service unavailable",
            "bad gateway",
            "overloaded",
            "too many requests",
            "rate_limit_error",
            "anthropic api error",
            "openai error",
        ]
    ):
        return "model_error"
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
        # Check if the failure is due to a known-flaky test command
        if usage_store is not None:
            for vr in result.validation_results:
                if vr.exit_code != 0 and usage_store.is_command_flaky(
                    vr.command, now=datetime.now(UTC)
                ):
                    return "flaky_test"
        return "validation_failure"
    # S7-3: Tool-level failures (bash/file/git tool errors distinct from auth)
    if any(
        token in excerpt
        for token in [
            "tool_error",
            "bash tool failed",
            "git tool failed",
            "permission denied",
            "read-only file system",
        ]
    ):
        return "tool_failure"
    # Infrastructure / auth / tooling failures
    if any(
        token in excerpt
        for token in [
            "api key not set",
            "authentication",
            "auth",
            "login required",
            "no such file or directory",
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


_BOARD_SNAPSHOT_ACTIVE_STATES = {"Running", "Ready for AI", "Blocked", "Review"}


def write_board_snapshot(
    issues: list[dict[str, Any]],
    *,
    role: str,
    status_dir: Path | None,
) -> None:
    if status_dir is None:
        return
    status_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = status_dir / "board_snapshot.json"
    tmp_path = status_dir / "board_snapshot.json.tmp"
    counts: dict[str, dict[str, int]] = {}
    active_issues: list[dict[str, Any]] = []
    for issue in issues:
        state = issue_status_name(issue)
        if state not in _BOARD_SNAPSHOT_ACTIVE_STATES:
            continue
        repo = "unknown"
        kind = ""
        for lbl in issue_label_names(issue):
            if lbl.lower().startswith("repo:"):
                repo = lbl.split(":", 1)[1].strip()
            elif lbl.lower().startswith("task-kind:"):
                kind = lbl.split(":", 1)[1].strip()
        if repo not in counts:
            counts[repo] = {"Running": 0, "Ready for AI": 0, "Blocked": 0, "Review": 0}
        if state in counts[repo]:
            counts[repo][state] += 1
        active_issues.append({
            "id": str(issue.get("id", "")),
            "name": str(issue.get("name", "")),
            "state": state,
            "repo": repo,
            "kind": kind,
        })
    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "written_by": role,
        "counts": counts,
        "issues": active_issues,
    }
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, snapshot_path)


_HEARTBEAT_MAX_AGE_SECONDS = 300  # 5 minutes


def _heartbeat_path(status_dir: Path, role: str) -> Path:
    return status_dir / f"heartbeat_{role}.json"


def write_heartbeat(status_dir: Path, role: str, *, now: datetime) -> None:
    """Write current timestamp to logs/local/heartbeat_{role}.json."""
    try:
        status_dir.mkdir(parents=True, exist_ok=True)
        _heartbeat_path(status_dir, role).write_text(
            json.dumps({"role": role, "ts": now.isoformat()})
        )
    except Exception:
        pass


def check_heartbeats(log_dir: Path, *, now: datetime | None = None) -> list[str]:
    """Return list of role names whose heartbeat is stale or missing."""
    from datetime import timezone

    now = now or datetime.now(UTC)
    stale: list[str] = []
    try:
        hb_files = list(log_dir.glob("heartbeat_*.json"))
    except Exception:
        return []
    for hb_file in hb_files:
        try:
            payload = json.loads(hb_file.read_text())
            ts = datetime.fromisoformat(str(payload["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).total_seconds()
            if age > _HEARTBEAT_MAX_AGE_SECONDS:
                stale.append(str(payload.get("role", hb_file.stem)))
        except Exception:
            stale.append(hb_file.stem)
    return stale


def execution_gate_decision(
    *,
    service: ExecutionService,
    role: str,
    action: str,
    issue: dict[str, Any],
    now: datetime | None = None,
    kodo_gate_check: Callable[[], tuple[bool, str]] | None = None,
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

    # S6-2: Per-repo daily execution cap.
    try:
        _rk = _extract_repo_key(issue, service)
        _repo_cfg_gate = service.settings.repos.get(_rk)
        _max_daily = getattr(_repo_cfg_gate, "max_daily_executions", None) if _repo_cfg_gate else None
        if _max_daily is not None:
            repo_budget = store.budget_decision_for_repo(_rk, _max_daily, now=now)
            if not repo_budget.allowed:
                store.record_skip(
                    role=role,
                    task_id=task_id,
                    signature=signature,
                    reason=repo_budget.reason or "repo_budget_exceeded",
                    detail=_rk,
                    now=now,
                    evidence={"repo_key": _rk, "limit": repo_budget.limit, "current": repo_budget.current},
                )
                return "skip_repo_budget", {
                    "reason": repo_budget.reason,
                    "repo_key": _rk,
                    "limit": repo_budget.limit,
                    "current": repo_budget.current,
                }
    except Exception:
        pass

    # S9-6: Budget allocation by acceptance rate.
    # When a family has low calibration ratio (< 0.5), it consumes double the execution
    # credit to slow down low-confidence families without fully blocking them.
    _extra_credit = False
    try:
        from control_plane.tuning.calibration import ConfidenceCalibrationStore, _EXPECTED_RATES
        _issue_labels = [
            str(lbl.get("name", lbl) if isinstance(lbl, dict) else lbl)
            for lbl in (issue.get("labels") or [])
        ]
        _family_for_cal = ""
        _confidence_for_cal = ""
        for _lbl in _issue_labels:
            if _lbl.startswith("source_family:"):
                _family_for_cal = _lbl.split(":", 1)[1].strip()
            elif _lbl in ("confidence:high", "confidence:medium", "confidence:low"):
                _confidence_for_cal = _lbl.split(":", 1)[1].strip()
        # Also check description text for source_family
        if not _family_for_cal:
            _desc = str(issue.get("description") or "")
            for _line in _desc.splitlines():
                if _line.strip().startswith("- source_family:"):
                    _family_for_cal = _line.split(":", 1)[1].strip()
                    break
        if _family_for_cal and _confidence_for_cal:
            _cal_store = ConfidenceCalibrationStore()
            _cal_rate = _cal_store.calibration_for(_family_for_cal, _confidence_for_cal)
            if _cal_rate is not None:
                _expected = _EXPECTED_RATES.get(_confidence_for_cal, 0.5)
                _ratio = _cal_rate / _expected if _expected > 0 else 1.0
                if _ratio < 0.5:
                    _extra_credit = True
                    _logger.info(json.dumps({
                        "event": "calibration_budget_penalty",
                        "task_id": task_id,
                        "family": _family_for_cal,
                        "confidence": _confidence_for_cal,
                        "calibration_ratio": round(_ratio, 3),
                        "reason": "low calibration ratio — recording extra execution credit",
                    }))
    except Exception:
        pass

    # Kodo concurrency / resource gate — checked last so it never records a
    # signature before kodo actually runs.  If blocked here, return a gate
    # action WITHOUT writing to the usage store so the next cycle retries
    # the full gate sequence rather than treating this task as a no-op.
    if kodo_gate_check is not None:
        _gate_ok, _gate_reason = kodo_gate_check()
        if not _gate_ok:
            return "kodo_gate_blocked", {"reason": _gate_reason}

    store.record_execution(role=role, task_id=task_id, signature=signature, now=now)
    if _extra_credit:
        # Record a second execution credit to slow down under-performing families
        store.record_execution(role=role, task_id=f"{task_id}_calibration_penalty", signature=signature, now=now)
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
    state = issue.get("state") or issue.get("state_detail")
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


def issue_urgency_score(issue: dict[str, Any]) -> int:
    """Return a composite urgency score (higher = more urgent).

    Combines priority label weight, title-prefix boost, and task age so that
    regression fixes and high-priority tasks are claimed before routine
    maintenance work — even when all sit in the same Ready-for-AI queue.
    """
    score = 0

    # Priority label weight
    pri = issue_priority(issue)  # 0=high, 1=medium, 2=low, 3=unset
    score += {0: 30, 1: 20, 2: 10, 3: 15}.get(pri, 15)

    # Title prefix boost — system-critical task types preempt general work
    title = str(issue.get("name", "")).strip().lower()
    if title.startswith("[regression]") or "post-merge regression" in title:
        score += 25
    elif title.startswith("[fix]") or title.startswith("[rebase]"):
        score += 15
    elif title.startswith("[revise]") or title.startswith("[verify]"):
        score += 8
    elif title.startswith("[workspace]") or title.startswith("[step 1"):
        score += 5

    # Task age: older Ready tasks get a small bump (capped at 3 days = +3)
    _created_raw = issue.get("created_at") or issue.get("updated_at")
    if _created_raw:
        try:
            from datetime import timezone as _tz
            _dt = datetime.fromisoformat(str(_created_raw).replace("Z", "+00:00"))
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=_tz.utc)
            score += min((datetime.now(UTC) - _dt).days, 3)
        except (ValueError, TypeError):
            pass

    return score


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
    VideoFoundry proposals and vice versa.
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


def active_task_count_from_issues(
    issues: list[dict[str, Any]],
    *,
    service: "ExecutionService | None" = None,
) -> int:
    """Count goal/test tasks currently in Ready for AI or Running state.

    Tasks that are Ready for AI but blocked by the self-modify guard (self-repo
    without approval) are excluded — they will never execute and should not
    prevent backlog promotion or new proposal generation.
    """
    count = 0
    for issue in issues:
        if issue_task_kind(issue) not in {"goal", "test"}:
            continue
        if issue_status_name(issue) not in {"Ready for AI", "Running"}:
            continue
        if service is not None and issue_status_name(issue) == "Ready for AI":
            repo_key = _extract_repo_key(issue, service)
            if _is_self_repo(repo_key, service) and not _self_modify_approved(issue):
                continue  # parked pending approval — don't count as active
        count += 1
    return count


def promote_backlog_tasks(
    client: PlaneClient,
    issues: list[dict[str, Any]],
    *,
    max_promotions: int = MAX_BACKLOG_PROMOTIONS_PER_CYCLE,
    service: "ExecutionService | None" = None,
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
        # Don't promote self-repo tasks that haven't been approved — they'd
        # immediately be skipped by the self-modify guard and re-block the board.
        if service is not None:
            repo_key = _extract_repo_key(issue, service)
            if _is_self_repo(repo_key, service) and not _self_modify_approved(issue):
                continue
        labels = issue_label_names(issue)
        source = next(
            (lbl.split(":", 1)[1].strip().lower() for lbl in labels if lbl.lower().startswith("source:")),
            "",
        )
        has_repo_label = any(lbl.lower().startswith("repo:") for lbl in labels)
        # Promote tasks with a known source label, OR tasks that have a repo: label
        # but no source: label — these are proposer-created tasks that predate
        # systematic source labeling and would otherwise sit in Backlog forever.
        # reviewer-dep-conflict and post-merge-ci are autonomy-generated and should
        # flow freely without waiting for a human to promote them.
        _AUTO_SOURCES = {"proposer", "autonomy", "improve-worker", "reviewer-dep-conflict", "post-merge-ci", "multi-step-plan", "spec-campaign"}
        if source in _AUTO_SOURCES or (has_repo_label and not source):
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
        allowed_kinds = ROLE_TASK_KINDS.get(role, {role})
        if task_kind not in allowed_kinds:
            continue
        if _has_active_pr_review(task_id):
            continue
        if status_name == ready_state:
            return task_id
        if not status_name or status_name == str(issue.get("state", "")):
            detailed_issue = client.fetch_issue(task_id)
            _allowed = ROLE_TASK_KINDS.get(role, {role})
            if issue_task_kind(detailed_issue) in _allowed and issue_status_name(detailed_issue) == ready_state:
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
            merged_sha = (pr.get("merge_commit_sha") or (pr.get("head") or {}).get("sha", ""))
            failed_checks = gh.get_failed_checks(owner, repo_name, pr_number, pr_data=pr)
            if not failed_checks:
                continue  # CI clean — nothing to do
            task_title = str(issue.get("name", "unknown task"))
            repo_key = _repo_key_from_pr_url(pr_url, service)
            # S6-7: Check whether this is a safe revert candidate (merge commit is
            # still HEAD of the base branch — no subsequent commits on top of it).
            base_branch = str((pr.get("base") or {}).get("ref", ""))
            _base_head = gh.get_branch_head(owner, repo_name, base_branch) if base_branch else None
            _is_safe_revert = bool(
                merged_sha and _base_head and merged_sha == _base_head
            )
            if _is_safe_revert:
                # S8-5: Auto-create a revert branch and open a PR immediately.
                # This acts faster than waiting for a Kodo execution cycle.
                _revert_branch = f"revert/{merged_sha[:8]}-{task_id[:8]}"
                _revert_pr_url: str | None = None
                repo_key_for_revert = _repo_key_from_pr_url(pr_url, service)
                repo_cfg_for_revert = service.settings.repos.get(repo_key_for_revert)
                if repo_cfg_for_revert and base_branch and merged_sha:
                    try:
                        _reverted = service.create_revert_branch(
                            clone_url=repo_cfg_for_revert.clone_url,
                            base_branch=base_branch,
                            merge_sha=merged_sha,
                            revert_branch=_revert_branch,
                        )
                        if _reverted:
                            _token = service.settings.repo_git_token(repo_key_for_revert)
                            if _token:
                                _rgh = GitHubPRClient(_token)
                                _rpr = _rgh.create_pr(
                                    owner, repo_name,
                                    head=_revert_branch,
                                    base=base_branch,
                                    title=f"Revert: {task_title[:80]}",
                                    body=(
                                        f"Automatic revert of merge commit `{merged_sha[:8]}`.\n\n"
                                        f"Post-merge CI failures detected on PR from task `{task_id}`:\n"
                                        + "\n".join(f"- {c}" for c in failed_checks[:3])
                                    ),
                                )
                                _revert_pr_url = _rpr.get("html_url", "")
                                _logger.info(json.dumps({
                                    "event": "auto_revert_pr_created",
                                    "task_id": task_id,
                                    "revert_pr_url": _revert_pr_url,
                                    "revert_branch": _revert_branch,
                                }))
                    except Exception as _exc:
                        _logger.warning(json.dumps({
                            "event": "auto_revert_pr_failed",
                            "task_id": task_id,
                            "error": str(_exc),
                        }))
                regression_goal = (
                    f"Revert the PR from task '{task_title}' to restore CI stability. "
                    "The merge commit is still the HEAD of the base branch, so a clean revert is safe. "
                    + (f"An automatic revert PR has been opened: {_revert_pr_url} — "
                       "review and merge it, or investigate whether CI was a transient flake."
                       if _revert_pr_url else
                       "Create a revert PR and merge it.")
                )
                regression_action = "revert"
            else:
                regression_goal = (
                    f"CI failed after merging the PR from task '{task_title}'. "
                    "Investigate the failing checks and either fix the regression or "
                    "revert the change if the fix is not straightforward."
                )
                regression_action = "fix_or_revert"
            regression_task = client.create_issue(
                name=f"Regression from: {task_title}",
                description=(
                    f"## Execution\nrepo: {repo_key}\nmode: goal\n\n"
                    f"## Goal\n{regression_goal}\n\n"
                    "## Context\n"
                    f"- source_task_id: {task_id}\n"
                    f"- pull_request_url: {pr_url}\n"
                    + (f"- merged_sha: {merged_sha}\n" if merged_sha else "")
                    + (f"- base_branch: {base_branch}\n" if base_branch else "")
                    + f"- failed_checks: {'; '.join(failed_checks[:3])}\n"
                    f"- recommended_action: {regression_action}\n"
                    f"- safe_revert: {'true' if _is_safe_revert else 'false'}\n"
                    "- priority: high\n"
                ),
                state="Ready for AI",
                label_names=["task-kind: goal", "priority: high", f"repo: {repo_key}", "source: post-merge-ci"]
                + (["self-modify: approved"] if _is_self_repo(repo_key, service) else []),
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
# Token/credential validation
# ---------------------------------------------------------------------------

def validate_credentials(
    settings: "Any",
    *,
    usage_store: "UsageStore",
    now: datetime,
) -> bool:
    """Validate GitHub and Plane API tokens.

    Returns True if all configured credentials are valid.  On 401/403 writes
    an escalation event and logs clearly.  Network errors are logged as warnings
    but do not block the loop (connectivity issue ≠ invalid token).
    """
    valid = True

    # GitHub
    token = settings.git_token() if callable(getattr(settings, "git_token", None)) else None
    if token:
        try:
            resp = httpx.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=10,
            )
            if resp.status_code in (401, 403):
                _logger.error(json.dumps({
                    "event": "credential_invalid",
                    "provider": "github",
                    "status": resp.status_code,
                }))
                usage_store.record_escalation(
                    classification="credential_github_invalid",
                    task_ids=[],
                    now=now,
                )
                valid = False
            else:
                # S7-2: Check token expiry from response header (fine-grained PATs).
                _expiry_header = resp.headers.get("x-token-expiration") or resp.headers.get("github-authentication-token-expiration")
                if _expiry_header:
                    try:
                        from datetime import timezone as _tz
                        _expiry_dt = datetime.fromisoformat(_expiry_header.replace("Z", "+00:00"))
                        if _expiry_dt.tzinfo is None:
                            _expiry_dt = _expiry_dt.replace(tzinfo=_tz.utc)
                        _days_left = (_expiry_dt - now).days
                        _esc = getattr(settings, "escalation", None)
                        _warn_days = int(getattr(_esc, "credential_expiry_warn_days", 7)) if _esc else 7
                        if _warn_days > 0 and _days_left <= _warn_days:
                            _level = "error" if _days_left <= 1 else "warning"
                            getattr(_logger, _level)(json.dumps({
                                "event": "credential_expiry_soon",
                                "provider": "github",
                                "days_left": _days_left,
                                "expires_at": _expiry_header,
                            }))
                            if _days_left <= 1:
                                usage_store.record_escalation(
                                    classification="credential_github_expiring",
                                    task_ids=[],
                                    now=now,
                                )
                    except Exception:
                        pass
        except Exception as exc:
            _logger.warning(json.dumps({
                "event": "credential_check_failed",
                "provider": "github",
                "error": str(exc),
            }))

    # Plane — use the project work-items endpoint for the credential check.
    # The workspace-level endpoint (/api/v1/workspaces/{slug}/) does not accept
    # API key auth in some Plane versions and returns 401 even with a valid token.
    # The work-items endpoint uses the same token and is a reliable reachability probe.
    try:
        plane_token = settings.plane_token()
        resp = httpx.get(
            f"{settings.plane.base_url}/api/v1/workspaces/{settings.plane.workspace_slug}/projects/{settings.plane.project_id}/work-items/",
            headers={"X-API-Key": plane_token},
            timeout=10,
        )
        if resp.status_code in (401, 403):
            _logger.error(json.dumps({
                "event": "credential_invalid",
                "provider": "plane",
                "status": resp.status_code,
            }))
            usage_store.record_escalation(
                classification="credential_plane_invalid",
                task_ids=[],
                now=now,
            )
            valid = False
    except Exception as exc:
        _logger.warning(json.dumps({
            "event": "credential_check_failed",
            "provider": "plane",
            "error": str(exc),
        }))

    return valid


# ---------------------------------------------------------------------------
# Pre-execution task validation (Item 2)
# ---------------------------------------------------------------------------

_MIN_GOAL_LENGTH = 30
_MAX_GOAL_LENGTH = 8000

# S9-2: Per-task-kind tool requirements for execution environment pre-flight.
# Keys are task families (extracted from source_family label).
# Values are lists of tool names; at least one in each sub-list must be present.
_KIND_REQUIRED_TOOLS: dict[str, list[list[str]]] = {
    "lint_fix": [["ruff"]],
    "type_fix": [["ty", "mypy"]],   # either ty OR mypy satisfies this requirement
    "test_fix": [["pytest"]],
    "coverage_gap": [["pytest", "coverage"]],
}


def _check_execution_environment(
    service: "ExecutionService",
    family: str,
) -> list[str]:
    """Return a list of missing tool warnings for the given proposal family.

    Uses shutil.which() for PATH lookup, then falls back to checking the first
    configured repo's venv if a local_path is available.  Returns [] when all
    required tools are present or the family has no requirements.
    """
    import shutil

    requirements = _KIND_REQUIRED_TOOLS.get(family, [])
    if not requirements:
        return []

    # Build venv bin paths from configured local_paths
    venv_paths: list[Path] = []
    for repo_cfg in service.settings.repos.values():
        lp = getattr(repo_cfg, "local_path", None)
        if lp:
            venv_bin = Path(str(lp)) / ".venv" / "bin"
            if venv_bin.is_dir():
                venv_paths.append(venv_bin)

    missing: list[str] = []
    for tool_group in requirements:
        # Each group is OR — at least one tool in the group must exist
        found_any = False
        for tool in tool_group:
            if shutil.which(tool):
                found_any = True
                break
            # Check venv paths
            for venv_bin in venv_paths:
                if (venv_bin / tool).exists():
                    found_any = True
                    break
            if found_any:
                break
        if not found_any:
            missing.append(f"required tool not found: {' or '.join(tool_group)}")
    return missing
_INVALID_GOAL_MARKERS = ("fix everything", "fix all", "improve everything", "do everything")

_PRE_EXEC_VALIDATION_MARKER = "[Goal] Task rejected by pre-execution validation"


def _check_kodo_execution_gate(settings: Any) -> tuple[bool, str]:
    """Return (allowed, reason) before launching a kodo process.

    Checks two independent conditions:
    - Concurrency: count live kodo-shim processes; block if at or above
      settings.max_concurrent_kodo (0 = unlimited).
    - Memory: read /proc/meminfo MemAvailable; block if below
      settings.min_kodo_available_mb (0 = disabled).
    """
    # --- concurrency gate ---
    max_kodo = getattr(settings, "max_concurrent_kodo", 1)
    if max_kodo > 0:
        try:
            count = sum(
                1
                for p in Path("/proc").iterdir()
                if p.name.isdigit()
                and "kodo" in (p / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
            )
        except OSError:
            count = 0
        if count >= max_kodo:
            return False, f"kodo_concurrency_cap (running={count}, max={max_kodo})"

    # --- memory gate ---
    min_mb = getattr(settings, "min_kodo_available_mb", 400)
    if min_mb > 0:
        try:
            available_kb = 0
            swap_free_kb = 0
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    available_kb = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free_kb = int(line.split()[1])
            available_mb = (available_kb + swap_free_kb) // 1024
            if available_mb < min_mb:
                return False, f"low_memory (available={available_mb}MB, min={min_mb}MB)"
        except OSError:
            pass

    return True, "ok"


def validate_task_pre_execution(
    client: PlaneClient,
    service: "ExecutionService",
    task_id: str,
    issue: dict[str, Any],
) -> bool:
    """Validate a task is actionable before claiming it for execution.

    Checks:
    1. Goal text is non-empty and within a useful length range.
    2. Goal text is not a vague catch-all phrase.
    3. If the goal mentions specific source files by extension, at least one
       of them exists in a configured repo's local_path.

    On failure posts a comment and moves the task to Backlog so it can be
    refined before retrying.  Returns True when the task passes all checks.
    """
    try:
        board_task = service.parse_task(client, task_id)
        goal_text = (board_task.goal_text or "").strip()
    except Exception:
        goal_text = str(issue.get("description") or issue.get("description_stripped") or "").strip()

    # If we cannot determine the goal at all, pass through rather than block.
    # Blocking unknown tasks would reject every task in environments that don't
    # carry explicit goal_text (e.g. bare Plane issues created by hand).
    if not goal_text:
        return True

    reasons: list[str] = []

    if len(goal_text) < _MIN_GOAL_LENGTH:
        reasons.append(f"goal text too short ({len(goal_text)} chars, minimum {_MIN_GOAL_LENGTH})")

    if len(goal_text) > _MAX_GOAL_LENGTH:
        reasons.append(f"goal text too long ({len(goal_text)} chars, maximum {_MAX_GOAL_LENGTH}) — split into sub-tasks")

    lower_goal = goal_text.lower()
    for marker in _INVALID_GOAL_MARKERS:
        if marker in lower_goal:
            reasons.append(f"goal contains vague catch-all phrase: '{marker}'")
            break

    # File-existence check: if goal mentions foo.py / bar.ts etc., at least one must exist.
    if not reasons:
        mentioned_files = re.findall(r"\b[\w/.-]+\.(?:py|ts|js|go|rs|java|rb|sh|yaml|yml|json|md)\b", goal_text)
        if mentioned_files:
            local_paths = [
                Path(str(repo_cfg.local_path))
                for repo_cfg in service.settings.repos.values()
                if getattr(repo_cfg, "local_path", None)
            ]
            if local_paths:
                found_any = any(
                    (lp / fname).exists() or any(lp.rglob(fname))
                    for fname in mentioned_files[:5]
                    for lp in local_paths
                )
                if not found_any:
                    reasons.append(
                        f"mentioned files not found in any configured local_path: {', '.join(mentioned_files[:3])}"
                    )

    # S9-2: Execution environment pre-flight — check required tools for this family.
    # This is a WARN-only check: missing tools are logged but do not block execution.
    # This allows tasks to proceed even when tool availability can't be determined.
    try:
        labels = [
            str(lbl.get("name", lbl) if isinstance(lbl, dict) else lbl)
            for lbl in (issue.get("labels") or [])
        ]
        _family = ""
        for lbl in labels:
            if lbl.startswith("source_family:"):
                _family = lbl.split(":", 1)[1].strip()
                break
        if not _family:
            # Try extracting from description
            for line in goal_text.splitlines():
                if line.strip().startswith("source_family:"):
                    _family = line.split(":", 1)[1].strip()
                    break
        if _family:
            env_warnings = _check_execution_environment(service, _family)
            for warn in env_warnings:
                _logger.warning(json.dumps({
                    "event": "execution_env_warning",
                    "task_id": task_id,
                    "family": _family,
                    "warning": warn,
                }))
    except Exception:
        pass

    if not reasons:
        return True

    # Reject: post comment, move to Backlog
    reason_text = "; ".join(reasons)
    try:
        client.comment_issue(
            task_id,
            render_worker_comment(
                _PRE_EXEC_VALIDATION_MARKER,
                [
                    f"task_id: {task_id}",
                    "task_kind: goal",
                    "result_status: backlog",
                    f"reason: {reason_text}",
                    "next_action: refine the goal text and re-queue to Ready for AI",
                ],
            ),
        )
        client.transition_issue(task_id, "Backlog")
    except Exception:
        pass
    _logger.info(json.dumps({
        "event": "pre_exec_validation_rejected",
        "task_id": task_id,
        "reasons": reasons,
    }))
    return False


# ---------------------------------------------------------------------------
# Feedback loop automation (Item 3)
# ---------------------------------------------------------------------------

_FEEDBACK_DIR = Path("state/proposal_feedback")
_FEEDBACK_LOOP_CYCLE_INTERVAL = 15
_FEEDBACK_LOOP_SCAN_MARKER = "[Improve] Feedback auto-recorded"
# Marker appended to comments when the stale-autonomy-scan cancels a task.
# The human-rejection capture skips tasks that carry this marker so they are
# NOT written to the permanent rejection store (they were cancelled by the
# system, not by a human).
_STALE_AUTONOMY_CANCEL_MARKER = "<!-- cp:stale-autonomy-scan -->"
_STALE_AUTONOMY_TASK_DAYS = 21
_STALE_AUTONOMY_SCAN_CYCLE_INTERVAL = 30
# S7-5: Dependency update scan runs every N improve cycles.
_DEPENDENCY_UPDATE_SCAN_CYCLE_INTERVAL = 50
_MAX_DEPENDENCY_UPDATE_TASKS_PER_SCAN = 2
# Stale global editable install cleanup runs every 100 improve cycles.
_STALE_EDITABLE_CLEANUP_CYCLE_INTERVAL = 100
# Stale blocked follow-up cancellation + validation re-queue runs every 30 improve cycles.
_STALE_BLOCKED_RECONCILE_CYCLE_INTERVAL = 30
# Label added to validation_failure blocked tasks after one automatic re-queue attempt.
_VALIDATION_REQUEUE_LABEL = "validation-requeue-attempted"
# Minimum age (hours) a validation_failure blocked task must be before automatic re-queue.
_VALIDATION_REQUEUE_MIN_AGE_HOURS = 8
# Proposal dedup window: suppress proposals whose work was completed within this many days.
_DONE_PROPOSAL_DEDUP_WINDOW_DAYS = 30


def handle_feedback_loop_scan(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    issues: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Auto-record feedback for Done tasks whose PR was merged or closed.

    Runs on a cycle gate in the goal or improve watcher.  For each Done issue
    that has a ``pull_request_url`` in its artifact and no existing feedback
    file in ``state/proposal_feedback/``, the PR state is fetched from GitHub.
    A feedback record is written automatically, closing the learning loop
    without requiring the operator to call the feedback CLI.

    Returns a list of task IDs for which feedback was recorded this cycle.
    """
    _now = now or datetime.now(UTC)
    if issues is None:
        issues = client.list_issues()

    recorded_ids: list[str] = []
    rejection_store = ProposalRejectionStore()

    # ---- Part A: record outcomes for Done tasks that have a merged/closed PR ----
    for issue in issues:
        status = issue_status_name(issue).lower()
        if status != "done":
            continue

        task_id = str(issue.get("id", ""))
        if not task_id:
            continue

        feedback_path = _FEEDBACK_DIR / f"{task_id}.json"
        if feedback_path.exists():
            continue  # Already recorded

        artifact = service.usage_store.get_task_artifact(task_id)
        if not artifact:
            continue
        pr_url = str(artifact.get("pull_request_url") or "")
        if not pr_url:
            continue

        pr_num = _pr_number_from_url(pr_url)
        if pr_num is None:
            continue

        repo_key = str(artifact.get("repo_key") or _extract_repo_key(issue, service))
        _rgit = getattr(service.settings, "repo_git_token", None)
        token = _rgit(repo_key) if callable(_rgit) else None
        if not token:
            _git = getattr(service.settings, "git_token", None)
            token = _git() if callable(_git) else None
        if not token:
            continue

        repo_cfg = service.settings.repos.get(repo_key)
        if not repo_cfg:
            continue

        try:
            owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
        except Exception:
            continue

        gh = GitHubPRClient(token)
        try:
            pr_data = gh.get_pr(owner, repo_name, pr_num)
        except Exception:
            continue

        pr_state = str(pr_data.get("state") or "").lower()
        merged = bool(pr_data.get("merged") or pr_data.get("merged_at"))

        if merged:
            outcome = "merged"
        elif pr_state == "closed":
            outcome = "abandoned"
        else:
            continue  # Still open — nothing to record yet

        try:
            _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
            import json as _json
            feedback_path.write_text(_json.dumps({
                "recorded_at": _now.isoformat(),
                "task_id": task_id,
                "outcome": outcome,
                "source": "feedback_loop_scan",
                "pr_number": pr_num,
                "pr_url": pr_url,
            }, indent=2))
            recorded_ids.append(task_id)
            # Also update proposal_success_rate via usage_store
            service.usage_store.record_proposal_outcome(
                category=issue_task_kind(issue),
                succeeded=(outcome == "merged"),
                now=_now,
            )
            _logger.info(json.dumps({
                "event": "feedback_auto_recorded",
                "task_id": task_id,
                "outcome": outcome,
                "pr_url": pr_url,
            }))
        except Exception:
            pass

    # ---- Part B: capture human rejections of autonomy proposals ----
    # Detect Cancelled tasks with "source: autonomy" label that were cancelled
    # by a human (not by the stale-autonomy-scan).  Write an abandoned feedback
    # record and permanently register the dedup_key in the rejection store so
    # that the proposer does not recreate the same proposal indefinitely.
    for issue in issues:
        status = issue_status_name(issue).lower()
        if status != "cancelled":
            continue

        labels = issue_label_names(issue)
        if not any("source: autonomy" in lbl.lower() for lbl in labels):
            continue

        task_id = str(issue.get("id", ""))
        if not task_id:
            continue

        # Skip if the cancellation was produced by our own stale-autonomy-scan
        try:
            comments = client.list_comments(task_id)
            if any(_STALE_AUTONOMY_CANCEL_MARKER in str(c.get("comment_html", "")) for c in comments):
                continue
        except Exception:
            continue

        feedback_path = _FEEDBACK_DIR / f"{task_id}.json"
        if feedback_path.exists():
            continue  # Already recorded

        # Extract the candidate_dedup_key from the task description so we can
        # write it to the permanent rejection store.
        description = str(issue.get("description") or issue.get("description_stripped") or "")
        dedup_key = ""
        for line in description.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("candidate_dedup_key:"):
                dedup_key = stripped.split(":", 1)[1].strip()
                break
            if stripped.startswith("- proposal_dedup_key:"):
                dedup_key = stripped.split(":", 1)[1].strip()
                break

        try:
            _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
            import json as _json2
            feedback_path.write_text(_json2.dumps({
                "recorded_at": _now.isoformat(),
                "task_id": task_id,
                "outcome": "abandoned",
                "source": "human_rejection_capture",
                "dedup_key": dedup_key,
                "task_title": str(issue.get("name", "")),
            }, indent=2))
            recorded_ids.append(task_id)
            # Permanently register so the proposer does not recreate this task
            if dedup_key:
                rejection_store.record_rejection(
                    dedup_key,
                    reason="human_cancelled_autonomy_task",
                    task_id=task_id,
                    task_title=str(issue.get("name", "")),
                    now=_now,
                )
            service.usage_store.record_proposal_outcome(
                category=issue_task_kind(issue),
                succeeded=False,
                now=_now,
            )
            _logger.info(json.dumps({
                "event": "human_rejection_recorded",
                "task_id": task_id,
                "dedup_key": dedup_key,
            }))
        except Exception:
            pass

    return recorded_ids


# ---------------------------------------------------------------------------
# Workspace health monitoring (Item 4)
# ---------------------------------------------------------------------------

_WORKSPACE_HEALTH_CYCLE_INTERVAL = 25
_WORKSPACE_HEALTH_MARKER = "[Improve] Workspace environment unhealthy"


def handle_workspace_health_check(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    now: datetime | None = None,
) -> list[str]:
    """Verify and repair the execution environment for each configured repo.

    For every repo in settings that has a ``local_path`` set, runs a minimal
    Python sanity check inside the repo's venv.  On failure, attempts to
    re-run ``bootstrap.prepare()`` to restore the environment.  If bootstrap
    also fails, creates a high-priority goal task so a human can investigate.

    Returns a list of newly created task IDs (one per unhealthy repo that
    couldn't be self-repaired).
    """
    from control_plane.adapters.workspace.bootstrap import RepoEnvironmentBootstrapper

    _now = now or datetime.now(UTC)
    created_ids: list[str] = []

    for repo_key, repo_cfg in service.settings.repos.items():
        local_path_str = getattr(repo_cfg, "local_path", None)
        if not local_path_str:
            continue
        repo_path = Path(local_path_str)
        if not repo_path.exists():
            continue

        # Determine venv python path
        venv_dir = getattr(repo_cfg, "venv_dir", ".venv")
        python_bin = repo_path / venv_dir / "bin" / "python"
        if not python_bin.exists():
            python_bin = repo_path / venv_dir / "Scripts" / "python.exe"  # Windows fallback

        # Quick sanity check: run python -c "import sys; sys.exit(0)"
        healthy = False
        if python_bin.exists():
            try:
                import subprocess
                proc = subprocess.run(
                    [str(python_bin), "-c", "import sys; sys.exit(0)"],
                    cwd=str(repo_path),
                    capture_output=True,
                    timeout=15,
                )
                healthy = proc.returncode == 0
            except Exception:
                healthy = False

        if healthy:
            continue

        _logger.warning(json.dumps({
            "event": "workspace_health_unhealthy",
            "repo_key": repo_key,
            "local_path": local_path_str,
        }))

        # Attempt bootstrap repair
        repaired = False
        try:
            bootstrapper = RepoEnvironmentBootstrapper()
            bootstrapper.prepare(
                repo_path,
                python_binary=getattr(repo_cfg, "python_binary", "python3"),
                venv_dir=venv_dir,
                install_dev_command=getattr(repo_cfg, "install_dev_command", None),
                enabled=getattr(repo_cfg, "bootstrap_enabled", True),
                bootstrap_commands=getattr(repo_cfg, "bootstrap_commands", None),
            )
            repaired = True
            _logger.info(json.dumps({
                "event": "workspace_health_repaired",
                "repo_key": repo_key,
            }))
        except Exception as exc:
            _logger.warning(json.dumps({
                "event": "workspace_health_repair_failed",
                "repo_key": repo_key,
                "error": str(exc)[:300],
            }))

        if repaired:
            continue

        # Bootstrap failed — create a task for human investigation
        existing_names = existing_issue_names(client)
        task_title = f"[Workspace] Repair environment for {repo_key}"
        if task_title.lower() in existing_names:
            continue

        try:
            new_task = client.create_issue(
                name=task_title,
                description=(
                    f"## Execution\nrepo: {repo_key}\nmode: goal\n\n"
                    "## Goal\n"
                    f"The workspace environment for `{repo_key}` at `{local_path_str}` "
                    "failed its health check and could not be automatically repaired via bootstrap.\n\n"
                    "Investigate and restore the Python venv or bootstrap configuration so tasks can execute.\n\n"
                    "## Context\n"
                    f"- repo_key: {repo_key}\n"
                    f"- local_path: {local_path_str}\n"
                    f"- venv_dir: {venv_dir}\n"
                    "- source: workspace_health_monitor\n"
                    "- priority: high\n"
                ),
                state="Ready for AI",
                label_names=["task-kind: goal", "priority: high", f"repo: {repo_key}", "source: improve-worker"]
                + (["self-modify: approved"] if _is_self_repo(repo_key, service) else []),
            )
            new_id = str(new_task.get("id", ""))
            if new_id:
                created_ids.append(new_id)
            _logger.info(json.dumps({
                "event": "workspace_health_task_created",
                "repo_key": repo_key,
                "task_id": new_id,
            }))
        except Exception:
            pass

    return created_ids


# ---------------------------------------------------------------------------
# S10-2: Awaiting-input scan — detect answered questions and re-queue tasks
# ---------------------------------------------------------------------------

_AWAITING_INPUT_SCAN_CYCLE_INTERVAL = 8
_AWAITING_INPUT_ANSWER_MARKER = "<!-- cp:answer: "


def handle_awaiting_input_scan(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    issues: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Re-queue blocked tasks whose ``awaiting_input`` question has been answered.

    Scans Blocked tasks whose last triage comment carries
    ``blocked_classification: awaiting_input``.  When a human comment posted
    *after* the triage comment is found, the task description is updated to
    inject the answer and the task is transitioned back to ``Ready for AI``.

    Returns a list of task IDs re-queued.
    """
    _now = now or datetime.now(UTC)
    if issues is None:
        issues = client.list_issues()

    requeued_ids: list[str] = []

    for issue in issues:
        if issue_status_name(issue).lower() != "blocked":
            continue
        task_id = str(issue.get("id", ""))
        if not task_id:
            continue

        try:
            comments = client.list_comments(task_id)
        except Exception:
            continue

        # Find the most recent triage comment with awaiting_input classification
        triage_comment_idx: int | None = None
        for idx, c in enumerate(comments):
            body = str(c.get("body") or "")
            if TRIAGE_COMMENT_MARKER in body and "awaiting_input" in body:
                triage_comment_idx = idx

        if triage_comment_idx is None:
            continue

        # Check if any human comment was posted AFTER the triage comment
        reviewer_cfg = service.settings.reviewer
        bot_logins: set[str] = set(reviewer_cfg.bot_logins)
        marker = _bot_marker_from_settings(service.settings) if hasattr(service.settings, "reviewer") else ""

        human_answers = [
            c for c in comments[triage_comment_idx + 1:]
            if not _is_bot_comment_simple(c, bot_logins, marker)
        ]
        if not human_answers:
            continue

        # Extract the answer text
        answer_text = str(human_answers[-1].get("body") or "").strip()
        if not answer_text:
            continue

        # Re-queue: update description to inject the answer and transition to Ready for AI
        description = issue_description_text(issue)
        answer_section = (
            f"\n\n## Human Answer\n"
            f"The following answer was provided in response to a clarification request:\n\n"
            f"{answer_text}\n\n"
            f"Use this answer to guide your implementation."
        )
        new_description = description.rstrip() + answer_section

        try:
            client.update_issue_description(task_id, new_description)
        except Exception:
            pass

        try:
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Improve] Awaiting-input answer received",
                    [
                        f"task_id: {task_id}",
                        "result_status: re_queued",
                        "reason: human answered the clarification question",
                        "next_action: task re-queued to Ready for AI with answer injected into description",
                    ],
                ),
            )
            client.transition_issue(task_id, "Ready for AI")
            requeued_ids.append(task_id)
            _logger.info(json.dumps({
                "event": "awaiting_input_requeued",
                "task_id": task_id,
                "answer_length": len(answer_text),
            }))
        except Exception as exc:
            _logger.warning(json.dumps({
                "event": "awaiting_input_requeue_failed",
                "task_id": task_id,
                "error": str(exc)[:200],
            }))

    return requeued_ids


def _bot_marker_from_settings(settings: "Any") -> str:
    """Safely extract the bot comment marker from settings."""
    try:
        return settings.reviewer.bot_comment_marker
    except Exception:
        return ""


def _is_bot_comment_simple(comment: dict, bot_logins: set[str], marker: str) -> bool:
    """Lightweight bot comment check used by the awaiting-input scan."""
    login = (comment.get("user") or {}).get("login", "")
    if login in bot_logins:
        return True
    if marker and marker in (comment.get("body") or ""):
        return True
    return False


# ---------------------------------------------------------------------------
# S10-10: Priority rescore scan — re-evaluate backlog autonomy task relevance
# ---------------------------------------------------------------------------

_PRIORITY_RESCORE_CYCLE_INTERVAL = 45


def handle_priority_rescore_scan(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    issues: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Re-score Backlog autonomy tasks to check whether their signal is still present.

    For each Backlog task with ``source: autonomy`` label, reads the current
    usage store and calibration to determine whether:

    - The signal that generated the proposal is no longer relevant (demote:
      add a ``signal_stale`` label and move to a lower priority).
    - A freshly seen high-confidence signal makes the task more urgent
      (promote: boost priority label).

    Returns a list of task IDs whose priority changed.
    """
    _now = now or datetime.now(UTC)
    if issues is None:
        issues = client.list_issues()

    from control_plane.tuning.calibration import ConfidenceCalibrationStore
    calib = ConfidenceCalibrationStore()
    changed_ids: list[str] = []

    for issue in issues:
        if issue_status_name(issue).lower() != "backlog":
            continue
        labels = issue_label_names(issue)
        if not any("source: autonomy" == lbl.strip().lower() for lbl in labels):
            continue

        task_id = str(issue.get("id", ""))
        if not task_id:
            continue

        # Extract family and repo_key from labels
        family = next(
            (lbl.split(":", 1)[1].strip() for lbl in labels if lbl.lower().startswith("task-kind:")),
            "",
        )
        repo_key = next(
            (lbl.split(":", 1)[1].strip() for lbl in labels if lbl.lower().startswith("repo:")),
            default_repo_key(service),
        )

        # Get calibration acceptance rate for this family
        acceptance_rate = calib.calibration_for(family, "high", repo_key=repo_key) if family else None

        # Check proposal success rate from usage store
        success_rate = service.usage_store.proposal_success_rate(family, now=_now) if family else None

        action: str | None = None
        new_label: str | None = None

        # Demote: family with very low acceptance or success rate
        if (acceptance_rate is not None and acceptance_rate < 0.2) or \
                (success_rate is not None and success_rate < 0.2):
            if not any("signal_stale" in lbl for lbl in labels):
                action = "demoted"
                new_label = "signal_stale"
        # Promote: family with high acceptance rate that's already in Backlog
        elif (acceptance_rate is not None and acceptance_rate >= 0.7):
            if not any("priority: high" in lbl.lower() for lbl in labels):
                action = "promoted"
                new_label = "priority: high"

        if action and new_label:
            try:
                # Add the new label
                existing_labels = [lbl for lbl in labels if lbl]
                existing_labels.append(new_label)
                client.update_issue_labels(task_id, existing_labels)
                client.comment_issue(
                    task_id,
                    render_worker_comment(
                        "[Improve] Priority rescore",
                        [
                            f"task_id: {task_id}",
                            f"action: {action}",
                            f"family: {family}",
                            f"acceptance_rate: {acceptance_rate}",
                            f"success_rate: {success_rate}",
                            f"label_added: {new_label}",
                            f"rescored_at: {_now.isoformat()}",
                        ],
                    ),
                )
                changed_ids.append(task_id)
                _logger.info(json.dumps({
                    "event": "priority_rescore",
                    "task_id": task_id,
                    "action": action,
                    "family": family,
                    "acceptance_rate": acceptance_rate,
                    "success_rate": success_rate,
                }))
            except Exception as exc:
                _logger.warning(json.dumps({
                    "event": "priority_rescore_failed",
                    "task_id": task_id,
                    "error": str(exc)[:200],
                }))

    return changed_ids


# ---------------------------------------------------------------------------
# Multi-step dependency planning (Item 8)
# ---------------------------------------------------------------------------

_MULTI_STEP_LABEL = "plan: multi-step"
_MULTI_STEP_TITLE_KEYWORDS = (
    "refactor", "migrate", "redesign", "modernize", "audit",
    "overhaul", "restructure", "rewrite",
)
_MULTI_STEP_PLAN_MARKER = "[Plan] Multi-step plan created"


def _is_multi_step_task(issue: dict[str, Any]) -> bool:
    """Return True when the task warrants a multi-step execution plan."""
    labels = [lbl.lower() for lbl in issue_label_names(issue)]
    # Sub-tasks created by a prior multi-step plan must never spawn another
    # plan — that would cause unbounded recursive decomposition.
    if "source: multi-step-plan" in labels:
        return False
    if _MULTI_STEP_LABEL in labels:
        return True
    title = str(issue.get("name") or "").lower()
    return any(kw in title for kw in _MULTI_STEP_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Stale autonomy task scan (Session 5 gap 2)
# ---------------------------------------------------------------------------

def handle_stale_autonomy_task_scan(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    now: datetime | None = None,
    stale_days: int = _STALE_AUTONOMY_TASK_DAYS,
) -> list[str]:
    """Cancel Backlog autonomy-proposed tasks whose underlying signal has expired.

    A task is considered stale when:
    - It carries a ``source: autonomy`` label
    - It is in ``Backlog`` state
    - Its ``created_at`` (or earliest timestamp available) is older than
      *stale_days* days

    Stale tasks are transitioned to ``Cancelled`` with a comment containing
    ``_STALE_AUTONOMY_CANCEL_MARKER`` so the feedback-loop scan skips them
    and does NOT write them to the permanent rejection store.  (The signal may
    have resolved but the proposal was never explicitly rejected by a human.)

    Returns a list of cancelled task IDs.
    """
    _now = now or datetime.now(UTC)
    cutoff = _now.timestamp() - (stale_days * 86400)
    cancelled_ids: list[str] = []

    for issue in client.list_issues():
        if issue_status_name(issue).lower() != "backlog":
            continue
        labels = issue_label_names(issue)
        if not any("source: autonomy" in lbl.lower() for lbl in labels):
            continue

        task_id = str(issue.get("id", ""))
        if not task_id:
            continue

        # Determine task age from created_at or updated_at
        created_raw = issue.get("created_at") or issue.get("updated_at") or ""
        if not created_raw:
            continue
        try:
            from datetime import timezone as _tz
            created_dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=_tz.utc)
            if created_dt.timestamp() > cutoff:
                continue  # Task is fresh enough
        except (ValueError, TypeError):
            continue

        # Cancel the task
        try:
            client.transition_issue(task_id, "Cancelled")
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Improve] Stale autonomy task cancelled",
                    [
                        f"task_id: {task_id}",
                        "result_status: cancelled",
                        f"reason: autonomy-proposed task sat in Backlog for >{stale_days} days — "
                        "underlying signal has likely been resolved or superseded",
                        "next_action: the proposer will re-create this task if the signal reappears",
                        f"<!-- {_STALE_AUTONOMY_CANCEL_MARKER} -->",
                    ],
                ),
            )
            cancelled_ids.append(task_id)
            _logger.info(json.dumps({
                "event": "stale_autonomy_task_cancelled",
                "task_id": task_id,
                "age_days": round((_now.timestamp() - created_dt.timestamp()) / 86400, 1),
            }))
        except Exception:
            pass

    return cancelled_ids


# ---------------------------------------------------------------------------
# S7-5: Dependency update loop
# ---------------------------------------------------------------------------

_REQUIREMENTS_GLOB_PATTERNS = [
    "requirements*.txt",
    "requirements/*.txt",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "package.json",
]


def _discover_requirements_paths(repo_path: Path) -> list[str]:
    """Return repo-relative paths to dependency manifests found in *repo_path*.

    Results are sorted and deduplicated.  Falls back to ``requirements.txt``
    when nothing is found so the task description always has at least one entry.
    """
    found: list[str] = []
    for pattern in _REQUIREMENTS_GLOB_PATTERNS:
        for match in sorted(repo_path.glob(pattern)):
            rel = str(match.relative_to(repo_path))
            if rel not in found:
                found.append(rel)
    return found or ["requirements.txt"]


def handle_dependency_update_scan(
    client: PlaneClient,
    service: "ExecutionService",
) -> list[str]:
    """Create Plane tasks for outdated pip/npm dependencies in local repos.

    For each repo configured with a ``local_path``, this scan:
    1. Runs ``pip list --outdated --format=json`` inside the repo's venv
       (or the active Python environment if no venv is found).
    2. Identifies packages with a major-version bump.
    3. Creates one bounded update task per package, up to
       ``_MAX_DEPENDENCY_UPDATE_TASKS_PER_SCAN`` per call.

    Only repos with a ``local_path`` setting are checked — repos without a
    locally checked-out copy are skipped silently.  All subprocess calls are
    best-effort; a failure on one repo does not prevent the others.

    Returns a list of created task IDs.
    """
    # Include terminal (Done/Cancelled) issues so that a previously-completed
    # "Update X from A to B" task suppresses re-creation of the identical task.
    # A version bump to a *different* target will have a different title and
    # won't be suppressed.
    existing_names = existing_issue_names(client, include_terminal=True)
    created_ids: list[str] = []

    for repo_key, repo_cfg in service.settings.repos.items():
        if len(created_ids) >= _MAX_DEPENDENCY_UPDATE_TASKS_PER_SCAN:
            break
        local_path = getattr(repo_cfg, "local_path", None)
        if not local_path:
            continue
        repo_path = Path(local_path)
        if not repo_path.exists():
            continue

        # Prefer the repo's own venv python; fall back to system python3.
        venv_python = repo_path / ".venv" / "bin" / "python"
        python_bin = str(venv_python) if venv_python.exists() else "python3"

        try:
            proc = subprocess.run(
                [python_bin, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(repo_path),
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            outdated = json.loads(proc.stdout)
        except Exception:
            continue

        for pkg in outdated:
            if len(created_ids) >= _MAX_DEPENDENCY_UPDATE_TASKS_PER_SCAN:
                break
            try:
                name = str(pkg.get("name", ""))
                current = str(pkg.get("version", ""))
                latest = str(pkg.get("latest_version", ""))
                if not name or not current or not latest:
                    continue
                # Only propose updates for major-version bumps to avoid noise.
                cur_major = int(current.split(".")[0])
                lat_major = int(latest.split(".")[0])
                if lat_major <= cur_major:
                    continue
                task_title = f"Update {name} from {current} to {latest} in {repo_key}"
                if task_title.lower() in existing_names:
                    continue
                base_branch = getattr(repo_cfg, "default_branch", "main")
                _req_paths = _discover_requirements_paths(repo_path)
                _allowed_paths_yaml = "\n".join(f"  - {p}" for p in _req_paths)
                new_issue = client.create_issue(
                    name=task_title,
                    description=(
                        f"## Execution\n"
                        f"repo: {repo_key}\n"
                        f"base_branch: {base_branch}\n"
                        f"mode: goal\n"
                        f"allowed_paths:\n{_allowed_paths_yaml}\n\n"
                        f"## Goal\n"
                        f"Update the `{name}` dependency from `{current}` to `{latest}` "
                        f"(major version bump). Bump the version pin in the relevant "
                        f"requirements file(s) and verify the upgrade does not break "
                        f"existing tests or imports. Do NOT modify source files beyond "
                        f"the requirements files listed in allowed_paths — if API "
                        f"callsites need updating, surface that as a separate concern "
                        f"in the PR description.\n\n"
                        f"## Constraints\n"
                        f"- source: dependency_update_scan\n"
                        f"- package: {name}\n"
                        f"- current_version: {current}\n"
                        f"- target_version: {latest}\n"
                        f"- Only edit files listed in allowed_paths (requirements files).\n"
                        f"- Run validation commands after upgrading to confirm no regressions.\n"
                    ),
                    state="Backlog",
                    label_names=[
                        "task-kind: goal",
                        f"repo: {repo_key}",
                        "source: autonomy",
                        "source_family: dependency_update",
                        *( ["self-modify: approved"] if _is_self_repo(repo_key, service) else []),
                    ],
                )
                if new_issue:
                    created_ids.append(str(new_issue.get("id")))
                    existing_names.add(task_title)
                    _logger.info(json.dumps({
                        "event": "dependency_update_task_created",
                        "repo_key": repo_key,
                        "package": name,
                        "current": current,
                        "latest": latest,
                        "task_id": str(new_issue.get("id")),
                    }))
            except Exception:
                continue

    return created_ids


# ---------------------------------------------------------------------------
# Stale blocked follow-up cancellation + validation failure re-queue
# ---------------------------------------------------------------------------

def reconcile_stale_blocked_issues(
    client: PlaneClient,
    *,
    ready_state: str = "Ready for AI",
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """Auto-cancel superseded Blocked follow-ups and re-queue stale validation failures.

    **Cancellation pass** — walks all Blocked issues and cancels any that are
    follow-up tasks for a source that is now Done/Cancelled.  Detects source
    linkage via:

    * ``original_task_id`` in the description (improve-worker "Resolve blocked X"
      tasks built by ``build_follow_up_description``).
    * ``source_task_id`` in the Constraints section (test-worker follow-up tasks
      whose ``constraints_text`` contains ``- source_task_id: {id}``).

    **Re-queue pass** — finds Blocked tasks whose most recent execution comment
    contains a retryable ``blocked_classification`` (``validation_failure`` or
    ``unknown``), are at least ``_VALIDATION_REQUEUE_MIN_AGE_HOURS`` old, and
    have not yet been re-queued (no ``validation-requeue-attempted`` label).
    Transitions them back to *ready_state* and adds the guard label so the same
    task is not re-queued a second time automatically.

    The ``unknown`` classification covers executions that failed before kodo
    could produce a meaningful result (e.g. arg-parse errors from a code bug).
    These are retryable once — if the bug is fixed, the task will succeed; if
    it blocks again the guard label prevents a second automatic retry.

    Returns a dict with keys ``"cancelled"`` and ``"requeued"``, each a list of
    task IDs affected.
    """
    _now = now or datetime.now(UTC)
    cancelled_ids: list[str] = []
    requeued_ids: list[str] = []

    issues = client.list_issues()

    # Pre-build terminal-status lookup to avoid individual fetches where possible.
    terminal_statuses: dict[str, str] = {}
    for _iss in issues:
        _s = issue_status_name(_iss).strip().lower()
        if _s in ("done", "cancelled"):
            terminal_statuses[str(_iss["id"])] = _s

    for issue in issues:
        if issue_status_name(issue).strip().lower() != "blocked":
            continue
        task_id = str(issue["id"])
        title = str(issue.get("name", "")).strip()

        # ------------------------------------------------------------------ #
        # Cancellation pass: find source task and check if it's terminal.
        # ------------------------------------------------------------------ #
        description = issue_description_text(issue)
        source_id = parse_context_value(description, "original_task_id")
        if not source_id:
            source_id = parse_context_value(description, "source_task_id")

        if source_id:
            source_terminal = terminal_statuses.get(source_id)
            if source_terminal is None:
                try:
                    src_issue = client.fetch_issue(source_id)
                    src_status = issue_status_name(src_issue).strip().lower()
                    if src_status in ("done", "cancelled"):
                        source_terminal = src_status
                except Exception:
                    pass

            if source_terminal:
                try:
                    client.transition_issue(task_id, "Cancelled")
                    client.comment_issue(
                        task_id,
                        render_worker_comment(
                            "[Improve] Stale blocked follow-up auto-cancelled",
                            [
                                f"task_id: {task_id}",
                                f"source_task_id: {source_id}",
                                f"source_status: {source_terminal}",
                                "result_status: cancelled",
                                "reason: source task is Done/Cancelled — this follow-up is no longer needed",
                                "next_action: no action required",
                            ],
                        ),
                    )
                    cancelled_ids.append(task_id)
                    _logger.info(json.dumps({
                        "event": "stale_blocked_follow_up_cancelled",
                        "task_id": task_id,
                        "task_title": title,
                        "source_task_id": source_id,
                        "source_status": source_terminal,
                    }))
                except Exception:
                    pass
                continue  # Don't also re-queue a task we just cancelled.

        # ------------------------------------------------------------------ #
        # Reverse cancellation pass: if THIS task created follow-ups and any
        # of those follow-ups are now terminal (Done/Cancelled), cancel this
        # task — its remediation work is complete.
        # ------------------------------------------------------------------ #
        # Extract follow_up_task_ids from comments on this blocked task.
        _follow_up_ids: list[str] = []
        try:
            for _c in client.list_comments(task_id):
                _ct = extract_comment_text(_c).lower()
                # Match "follow_up_task_ids: id1, id2" lines
                for _line in _ct.splitlines():
                    if "follow_up_task_ids:" in _line:
                        _raw = _line.split("follow_up_task_ids:", 1)[1].strip()
                        for _fid in _raw.split(","):
                            _fid = _fid.strip()
                            if _fid and _fid != "none":
                                _follow_up_ids.append(_fid)
        except Exception:
            pass

        _terminal_followup_id: str | None = None
        for _fid in _follow_up_ids:
            _fstatus = terminal_statuses.get(_fid)
            if _fstatus is None:
                try:
                    _fissue = client.fetch_issue(_fid)
                    _fs = issue_status_name(_fissue).strip().lower()
                    if _fs in ("done", "cancelled"):
                        _fstatus = _fs
                        terminal_statuses[_fid] = _fs
                except Exception:
                    pass
            if _fstatus:
                _terminal_followup_id = _fid
                break

        if _terminal_followup_id:
            try:
                client.transition_issue(task_id, "Cancelled")
                client.comment_issue(
                    task_id,
                    render_worker_comment(
                        "[Improve] Blocked source task auto-cancelled — follow-up resolved",
                        [
                            f"task_id: {task_id}",
                            f"follow_up_task_id: {_terminal_followup_id}",
                            f"follow_up_status: {terminal_statuses.get(_terminal_followup_id, 'terminal')}",
                            "result_status: cancelled",
                            "reason: this task's follow-up work is Done/Cancelled — no further action needed on the source",
                            "next_action: no action required",
                        ],
                    ),
                )
                cancelled_ids.append(task_id)
                _logger.info(json.dumps({
                    "event": "blocked_source_cancelled_followup_resolved",
                    "task_id": task_id,
                    "task_title": title,
                    "follow_up_task_id": _terminal_followup_id,
                }))
            except Exception:
                pass
            continue  # Don't also re-queue a task we just cancelled.

        # ------------------------------------------------------------------ #
        # Re-queue pass: retryable blocked tasks that are old enough.
        # Retryable classifications: validation_failure, unknown.
        # Non-retryable (handled elsewhere or permanently broken):
        #   retry_cap_exceeded, pre_existing_validation, parse_config.
        # ------------------------------------------------------------------ #
        _RETRYABLE_CLASSIFICATIONS = {"validation_failure", "unknown"}

        labels = {lbl.strip().lower() for lbl in issue_label_names(issue)}
        if _VALIDATION_REQUEUE_LABEL in labels:
            continue  # Already had one automatic re-queue — needs manual triage.

        updated_raw = issue.get("updated_at") or issue.get("created_at") or ""
        if not updated_raw:
            continue
        try:
            updated_dt = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
            if updated_dt.tzinfo is None:
                from datetime import timezone as _tz2
                updated_dt = updated_dt.replace(tzinfo=_tz2.utc)
            age_hours = (_now.timestamp() - updated_dt.timestamp()) / 3600
            if age_hours < _VALIDATION_REQUEUE_MIN_AGE_HOURS:
                continue
        except (ValueError, TypeError):
            continue

        comments = client.list_comments(task_id)
        retryable_classification: str | None = None
        for c in comments:
            text = extract_comment_text(c).lower()
            for cls in _RETRYABLE_CLASSIFICATIONS:
                if f"blocked_classification: {cls}" in text:
                    retryable_classification = cls
                    break
            if retryable_classification:
                break
        if not retryable_classification:
            continue

        try:
            new_labels = list(issue_label_names(issue)) + [_VALIDATION_REQUEUE_LABEL]
            client.update_issue_labels(task_id, new_labels)
            client.transition_issue(task_id, ready_state)
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Improve] Stale blocked task re-queued for one retry",
                    [
                        f"task_id: {task_id}",
                        "result_status: ready_for_ai",
                        f"blocked_classification: {retryable_classification}",
                        f"reason: {retryable_classification} block is >{_VALIDATION_REQUEUE_MIN_AGE_HOURS}h old — re-queueing for one retry",
                        "note: label 'validation-requeue-attempted' added; if this blocks again it will require manual triage",
                    ],
                ),
            )
            requeued_ids.append(task_id)
            _logger.info(json.dumps({
                "event": "stale_blocked_requeued",
                "task_id": task_id,
                "task_title": title,
                "blocked_classification": retryable_classification,
                "age_hours": round(age_hours, 1),
            }))
        except Exception:
            pass

    return {"cancelled": cancelled_ids, "requeued": requeued_ids}


# ---------------------------------------------------------------------------
# Campaign tracker completion
# ---------------------------------------------------------------------------

def reconcile_campaign_trackers(client: PlaneClient) -> list[str]:
    """Close [Campaign] parent tasks when all their child tasks are terminal.

    The campaign builder creates a ``[Campaign] <slug>`` parent issue labelled
    ``source: spec-campaign`` and ``campaign-id: <uuid>``.  Child tasks carry
    the same ``campaign-id`` label.  Nothing in the normal worker flow ever
    closes the parent — it sits in Backlog/RFA indefinitely even after every
    child is Done or Cancelled.

    This function finds campaign tracker issues whose every sibling (same
    campaign-id label, excluding the tracker itself) is in a terminal state,
    then transitions the tracker to Done.

    Returns a list of closed campaign tracker task IDs.
    """
    closed: list[str] = []
    try:
        all_issues = client.list_issues()
    except Exception:
        return closed

    # Group issues by campaign-id label value.
    campaign_issues: dict[str, list[dict[str, Any]]] = {}
    for iss in all_issues:
        for lbl in issue_label_names(iss):
            if lbl.lower().startswith("campaign-id:"):
                cid = lbl.split(":", 1)[1].strip()
                campaign_issues.setdefault(cid, []).append(iss)

    for cid, members in campaign_issues.items():
        # Find the tracker (title starts with "[Campaign]").
        tracker: dict[str, Any] | None = None
        children: list[dict[str, Any]] = []
        for iss in members:
            name = str(iss.get("name", "")).strip()
            if name.startswith("[Campaign]"):
                tracker = iss
            else:
                children.append(iss)

        if tracker is None or not children:
            continue

        tracker_status = issue_status_name(tracker).strip().lower()
        if tracker_status in ("done", "cancelled"):
            continue  # Already closed.

        # All children must be terminal.
        if not all(issue_status_name(c).strip().lower() in ("done", "cancelled") for c in children):
            continue

        tracker_id = str(tracker["id"])
        tracker_title = str(tracker.get("name", "")).strip()
        child_summary = ", ".join(
            f"{str(c.get('name',''))[:40]} ({issue_status_name(c)})"
            for c in children
        )
        try:
            client.transition_issue(tracker_id, "Done")
            client.comment_issue(
                tracker_id,
                render_worker_comment(
                    "[Campaign] All child tasks resolved — campaign complete",
                    [
                        f"campaign_id: {cid}",
                        f"child_count: {len(children)}",
                        f"children: {child_summary}",
                        "result_status: done",
                        "reason: every child task is Done or Cancelled",
                    ],
                ),
            )
            closed.append(tracker_id)
            _logger.info(json.dumps({
                "event": "campaign_tracker_closed",
                "campaign_id": cid,
                "tracker_id": tracker_id,
                "tracker_title": tracker_title,
                "child_count": len(children),
            }))
        except Exception:
            pass

    return closed


# ---------------------------------------------------------------------------
# Stale global editable install cleanup
# ---------------------------------------------------------------------------

def cleanup_stale_global_editables() -> list[str]:
    """Remove stale editable installs from the global Python site-packages.

    kodo's internal bootstrapping occasionally runs ``pip install -e .`` using
    the global (pyenv) Python without a virtualenv, depositing an editable
    install pointer in the global site-packages.  When the temp dir that kodo
    used is later removed, the editable entry becomes a dangling reference that
    confuses ``pip``.

    This function locates all editable installs in the global Python whose
    ``editable_project_location`` no longer exists on disk and uninstalls them.
    Returns the list of package names that were removed.

    Also removes aged ``/tmp/cp-task-*`` scratch directories that are more
    than 24 hours old and were not cleaned up by the kodo adapter.
    """
    global_python = shutil.which("python3") or ""
    removed: list[str] = []

    # --- stale editable installs ---
    if global_python:
        try:
            result = subprocess.run(
                [global_python, "-m", "pip", "list", "--format=json"],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "PIP_REQUIRE_VIRTUALENV": "0"},
            )
            if result.returncode == 0:
                packages = json.loads(result.stdout)
                stale = [
                    pkg["name"]
                    for pkg in packages
                    if "editable_project_location" in pkg
                    and pkg["editable_project_location"]
                    and not Path(pkg["editable_project_location"]).exists()
                ]
                for pkg_name in stale:
                    uninstall = subprocess.run(
                        [global_python, "-m", "pip", "uninstall", "-y", pkg_name],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        env={**os.environ, "PIP_REQUIRE_VIRTUALENV": "0"},
                    )
                    if uninstall.returncode == 0:
                        removed.append(pkg_name)
        except Exception:
            pass

    # --- aged /tmp/cp-task-* scratch dirs ---
    try:
        cutoff = time.time() - 86400  # 24 hours
        for entry in Path("/tmp").glob("cp-task-*"):
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass

    return removed


# ---------------------------------------------------------------------------
# S6-10: Board health snapshot
# ---------------------------------------------------------------------------

def board_health_check(
    issues: list[dict[str, Any]],
    service: "ExecutionService",
) -> list[dict[str, Any]]:
    """Return a list of board anomaly descriptors.

    Checks for:
    - Tasks stuck in "Running" state (no watcher should leave tasks running).
    - Clustered blocked reasons (same classification on >=5 blocked tasks).
    - Quiet propose lane (no Backlog/Ready tasks for >1 repo at once).

    Returns an empty list when the board looks healthy.
    """
    anomalies: list[dict[str, Any]] = []

    running_ids = [
        str(iss.get("id", ""))
        for iss in issues
        if issue_status_name(iss) == "Running"
    ]
    if len(running_ids) >= 3:
        anomalies.append({
            "kind": "stuck_running",
            "count": len(running_ids),
            "task_ids": running_ids[:10],
            "detail": "multiple tasks in Running state — a watcher may have crashed",
        })

    # Blocked reason clustering
    blocked_reasons: dict[str, list[str]] = {}
    for iss in issues:
        if issue_status_name(iss) != "Blocked":
            continue
        tid = str(iss.get("id", ""))
        for lbl in issue_label_names(iss):
            if lbl.startswith("blocked:") or lbl.startswith("classification:"):
                blocked_reasons.setdefault(lbl, []).append(tid)
    for reason, ids in blocked_reasons.items():
        if len(ids) >= 5:
            anomalies.append({
                "kind": "clustered_blocked_reason",
                "reason": reason,
                "count": len(ids),
                "task_ids": ids[:10],
                "detail": ">=5 blocked tasks share the same reason — systemic issue suspected",
            })

    # Quiet propose lane: count active (non-Done, non-Cancelled) tasks per repo
    repo_active: dict[str, int] = {}
    for iss in issues:
        status = issue_status_name(iss)
        if status in ("Done", "Cancelled"):
            continue
        for lbl in issue_label_names(iss):
            if lbl.startswith("repo:"):
                rk = lbl[5:].strip()
                repo_active[rk] = repo_active.get(rk, 0) + 1
    for rk in service.settings.repos:
        if repo_active.get(rk, 0) == 0:
            anomalies.append({
                "kind": "quiet_repo_lane",
                "repo_key": rk,
                "detail": f"no active tasks for repo '{rk}' — propose lane may be stalled",
            })

    return anomalies


def build_multi_step_plan(
    client: PlaneClient,
    service: "ExecutionService",
    task_id: str,
    issue: dict[str, Any],
) -> list[str]:
    """Decompose a complex task into Analyze → Implement → Verify subtasks.

    When *issue* qualifies as multi-step (keyword in title or explicit label),
    creates three dependent tasks:
    - ``[Step 1/3: Analyze] <title>`` — scoped investigation, no code changes
    - ``[Step 2/3: Implement] <title>`` — depends_on step 1
    - ``[Step 3/3: Verify] <title>`` — depends_on step 2

    Moves the original task to Backlog (it is superseded by the plan).
    Returns the list of created task IDs (empty if the task does not qualify
    or if any of the step tasks already exist on the board).
    """
    if not _is_multi_step_task(issue):
        return []

    original_title = str(issue.get("name") or "task")
    repo_key = _extract_repo_key(issue, service)
    base_labels = ["task-kind: goal", f"repo: {repo_key}", "source: multi-step-plan"]
    if _self_modify_approved(issue):
        base_labels.append("self-modify: approved")

    existing_names = existing_issue_names(client)

    step_titles = [
        f"[Step 1/3: Analyze] {original_title}",
        f"[Step 2/3: Implement] {original_title}",
        f"[Step 3/3: Verify] {original_title}",
    ]
    if any(t.lower() in existing_names for t in step_titles):
        return []  # Plan already created

    try:
        board_task = service.parse_task(client, task_id)
        goal_text = board_task.goal_text or ""
    except Exception:
        goal_text = ""

    created_ids: list[str] = []
    prev_id: str | None = None

    step_goals = [
        (
            f"Analyze the scope of: {original_title}\n\n"
            f"Original goal: {goal_text[:600]}\n\n"
            "Produce a written plan (as a Plane comment or artifact) that:\n"
            "- identifies the files/modules to change\n"
            "- lists the concrete changes needed\n"
            "- flags any risks or dependencies\n"
            "Do NOT make any code changes in this step."
        ),
        (
            f"Implement the changes for: {original_title}\n\n"
            f"Original goal: {goal_text[:600]}\n\n"
            "Follow the analysis from [Step 1/3: Analyze].  Make only the changes "
            "identified there.  Run existing tests after each significant change."
        ),
        (
            f"Verify the implementation for: {original_title}\n\n"
            "Run the full validation suite and confirm:\n"
            "- all pre-existing tests pass\n"
            "- the original goal is satisfied\n"
            "- no regressions introduced\n"
            "Fix any failures found during verification."
        ),
    ]

    for i, (step_title, step_goal) in enumerate(zip(step_titles, step_goals)):
        depends_line = f"\n- depends_on: {prev_id}" if prev_id else ""
        description = (
            f"## Execution\nrepo: {repo_key}\nmode: goal\n\n"
            f"## Goal\n{step_goal}\n\n"
            f"## Constraints\n"
            f"- source_task_id: {task_id}\n"
            f"- step: {i + 1} of 3\n"
            f"- plan_title: {original_title}\n"
            f"{depends_line}"
        )
        # Steps 2 and 3 start in Backlog; step 1 is Ready for AI
        state = "Ready for AI" if i == 0 else "Backlog"
        try:
            new_task = client.create_issue(
                name=step_title,
                description=description,
                state=state,
                label_names=base_labels,
            )
            new_id = str(new_task.get("id", ""))
            created_ids.append(new_id)
            prev_id = new_id
        except Exception as exc:
            _logger.warning(json.dumps({
                "event": "multi_step_plan_step_failed",
                "step": i + 1,
                "error": str(exc)[:200],
            }))
            break

    if created_ids:
        try:
            client.transition_issue(task_id, "Backlog")
            client.comment_issue(
                task_id,
                render_worker_comment(
                    _MULTI_STEP_PLAN_MARKER,
                    [
                        f"task_id: {task_id}",
                        "result_status: backlog",
                        f"reason: complex task decomposed into {len(created_ids)}-step plan",
                        f"step_task_ids: {', '.join(created_ids)}",
                        "next_action: step 1 is Ready for AI; steps 2 and 3 will activate after each predecessor completes",
                    ],
                ),
            )
        except Exception:
            pass
        _logger.info(json.dumps({
            "event": "multi_step_plan_created",
            "source_task_id": task_id,
            "step_task_ids": created_ids,
        }))
        # S10-4: Register the campaign so progress can be tracked via the
        # campaign-status CLI without navigating individual Plane tasks.
        try:
            from control_plane.execution.campaign_store import CampaignStore
            CampaignStore().create(
                source_task_id=task_id,
                title=original_title,
                step_task_ids=created_ids,
            )
        except Exception:
            pass

    return created_ids


# ---------------------------------------------------------------------------
# PR review revision cycle
# ---------------------------------------------------------------------------

_REVIEW_REVISION_MARKER = "[Improve] PR review revision"
_IN_REVIEW_STATES = {"in review", "review", "in_review"}


def handle_review_revision_scan(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    issues: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Create [Revise] tasks for issues in review with CHANGES_REQUESTED.

    Returns list of newly created task IDs.
    """
    if issues is None:
        issues = client.list_issues()

    reviewer_cfg = service.settings.reviewer
    bot_logins = {lbl.lower() for lbl in reviewer_cfg.bot_logins}
    allowed_logins = {lbl.lower() for lbl in reviewer_cfg.allowed_reviewer_logins}
    created_ids: list[str] = []
    existing_names = existing_issue_names(client)

    for issue in issues:
        status = issue_status_name(issue).lower()
        if status not in _IN_REVIEW_STATES:
            continue
        task_id = str(issue.get("id", ""))
        issue_title = str(issue.get("name", ""))
        repo_key = _extract_repo_key(issue, service)

        artifact = service.usage_store.get_task_artifact(task_id)
        if not artifact:
            continue
        pr_url = str(artifact.get("pull_request_url") or "")
        if not pr_url:
            continue

        pr_num = _pr_number_from_url(pr_url)
        if pr_num is None:
            continue

        repo_cfg = service.settings.repos.get(repo_key)
        if not repo_cfg:
            continue
        token = service.settings.repo_git_token(repo_key)
        if not token:
            continue
        gh = GitHubPRClient(token)
        try:
            owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
        except Exception:
            continue

        try:
            reviews = gh.list_pr_reviews(owner, repo_name, pr_num)
        except Exception:
            continue

        changes_requested = [
            r for r in reviews
            if r.get("state") == "CHANGES_REQUESTED"
            and r.get("user", {}).get("login", "").lower() not in bot_logins
            and (not allowed_logins or r.get("user", {}).get("login", "").lower() in allowed_logins)
        ]
        if not changes_requested:
            continue

        revision_title = f"[Revise] {issue_title} — address review feedback"
        if revision_title.strip().lower() in existing_names:
            continue

        # Summarise review comments for context
        review_summary_parts: list[str] = []
        try:
            inline = gh.list_pr_review_comments(owner, repo_name, pr_num)
            for c in inline[:10]:
                body = str(c.get("body") or "").strip()
                path = str(c.get("path") or "").strip()
                if body:
                    review_summary_parts.append(f"- [{path}] {body[:200]}")
        except Exception:
            pass
        for r in changes_requested[:3]:
            body = str(r.get("body") or "").strip()
            reviewer = str(r.get("user", {}).get("login") or "reviewer")
            if body:
                review_summary_parts.append(f"- [{reviewer}] {body[:300]}")
        review_summary = "\n".join(review_summary_parts)[:1200] if review_summary_parts else "(see PR for details)"

        new_task = client.create_issue(
            name=revision_title,
            description=(
                f"## Execution\nrepo: {repo_key}\nmode: goal\n\n"
                f"## Goal\n"
                f"Address the review feedback on PR {pr_url} for task '{issue_title}'.\n\n"
                f"## Review feedback\n{review_summary}\n\n"
                "## Context\n"
                f"- source_task_id: {task_id}\n"
                f"- pr_url: {pr_url}\n"
                "- source: review_revision_scan\n"
            ),
            state="Ready for AI",
            label_names=["task-kind: goal", f"repo: {repo_key}", "source: improve-worker"]
            + (["self-modify: approved"] if _is_self_repo(repo_key, service) else []),
        )
        created_ids.append(str(new_task.get("id", "")))
        _logger.info(json.dumps({
            "event": "review_revision_task_created",
            "task_id": task_id,
            "revision_task_id": new_task.get("id"),
            "pr_url": pr_url,
        }))

    return created_ids


# ---------------------------------------------------------------------------
# Merge conflict detection + rebase + stale PR TTL
# ---------------------------------------------------------------------------

_MERGE_CONFLICT_MARKER = "[Improve] PR merge conflict detected"
_STALE_PR_COMMENT_MARKER = "[Improve] Stale PR closed"
_STALE_PR_SCAN_CYCLE_INTERVAL = 20


def _pr_number_from_url(pr_url: str) -> int | None:
    m = re.search(r"/pull/(\d+)$", pr_url)
    return int(m.group(1)) if m else None


def _gh_client_for_repo(service: "ExecutionService", repo_key: str) -> "GitHubPRClient | None":
    token = service.settings.repo_git_token(repo_key)
    if not token:
        return None
    return GitHubPRClient(token)


def handle_merge_conflict_scan(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    issues: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Scan open PRs for merge conflicts; attempt rebase or create a task.

    Returns list of newly created task IDs.
    """
    from control_plane.adapters.git.client import GitClient

    created_ids: list[str] = []
    git = GitClient()

    for repo_key, repo_cfg in service.settings.repos.items():
        token = service.settings.repo_git_token(repo_key)
        if not token:
            continue
        gh = GitHubPRClient(token)
        try:
            owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
        except Exception:
            continue

        try:
            open_prs = gh.list_open_prs(owner, repo_name)
        except Exception:
            continue

        for pr in open_prs:
            pr_number = pr.get("number")
            pr_url = pr.get("html_url", "")
            if not pr_number or not pr_url:
                continue
            mergeable = gh.get_mergeable(owner, repo_name, pr_number)
            if mergeable is None or mergeable is True:
                continue  # True = fine; None = GitHub still computing

            local_path = getattr(repo_cfg, "local_path", None)
            if local_path:
                repo_path = Path(local_path)
                pr_branch = (pr.get("head") or {}).get("ref", "")
                base_branch = (pr.get("base") or {}).get("ref", repo_cfg.default_branch)
                if pr_branch:
                    try:
                        git.checkout_branch(repo_path, pr_branch)
                        ok = git.rebase_onto_origin(repo_path, base_branch)
                        if ok:
                            git.push_branch_force(repo_path, pr_branch)
                            _logger.info(json.dumps({
                                "event": "merge_conflict_rebased",
                                "repo": repo_key,
                                "pr_number": pr_number,
                                "branch": pr_branch,
                            }))
                            continue
                    except Exception as exc:
                        _logger.info(json.dumps({
                            "event": "merge_conflict_rebase_failed",
                            "repo": repo_key,
                            "pr_number": pr_number,
                            "error": str(exc),
                        }))

            # Rebase failed or no local path — create a conflict-fix task
            pr_title = pr.get("title", f"PR #{pr_number}")
            task_title = f"[Rebase] {pr_title}"
            existing_names = existing_issue_names(client)
            if task_title.strip().lower() in existing_names:
                continue
            new_task = client.create_issue(
                name=task_title,
                description=(
                    f"## Execution\nrepo: {repo_key}\nmode: goal\n\n"
                    f"## Goal\n"
                    f"The pull request '{pr_title}' ({pr_url}) has a merge conflict with "
                    f"`{repo_cfg.default_branch}`. Resolve the conflict, commit the resolution, "
                    "and push the branch so the PR can proceed to review.\n\n"
                    "## Context\n"
                    f"- pr_url: {pr_url}\n"
                    f"- pr_number: {pr_number}\n"
                    f"- base_branch: {repo_cfg.default_branch}\n"
                    "- source: merge_conflict_scan\n"
                ),
                state="Ready for AI",
                label_names=["task-kind: goal", f"repo: {repo_key}", "source: improve-worker"]
                + (["self-modify: approved"] if _is_self_repo(repo_key, service) else []),
            )
            created_ids.append(str(new_task.get("id", "")))
            _logger.info(json.dumps({
                "event": "merge_conflict_task_created",
                "repo": repo_key,
                "pr_number": pr_number,
                "task_id": new_task.get("id"),
            }))
    return created_ids


def handle_stale_pr_scan(
    client: PlaneClient,
    service: "ExecutionService",
    *,
    issues: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Close PRs older than stale_pr_days; requeue the originating task.

    Returns list of task IDs acted upon.
    """
    from datetime import timedelta
    from control_plane.adapters.git.client import GitClient

    now = now or datetime.now(UTC)
    stale_days = max(1, getattr(service.settings, "stale_pr_days", 7))
    cutoff = now - timedelta(days=stale_days)
    acted: list[str] = []
    git = GitClient()

    for repo_key, repo_cfg in service.settings.repos.items():
        token = service.settings.repo_git_token(repo_key)
        if not token:
            continue
        gh = GitHubPRClient(token)
        try:
            owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
        except Exception:
            continue
        try:
            open_prs = gh.list_open_prs(owner, repo_name)
        except Exception:
            continue

        for pr in open_prs:
            pr_number = pr.get("number")
            pr_url = pr.get("html_url", "")
            created_at_str = pr.get("created_at", "")
            if not pr_number or not pr_url or not created_at_str:
                continue
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_at > cutoff:
                    continue
            except ValueError:
                continue

            # Already commented → skip
            try:
                existing_comments = gh.list_pr_comments(owner, repo_name, pr_number)
                if any(_STALE_PR_COMMENT_MARKER in (c.get("body") or "") for c in existing_comments):
                    continue
            except Exception:
                pass

            # Try rebase first
            local_path = getattr(repo_cfg, "local_path", None)
            rebased = False
            if local_path:
                pr_branch = (pr.get("head") or {}).get("ref", "")
                base_branch = (pr.get("base") or {}).get("ref", repo_cfg.default_branch)
                if pr_branch:
                    try:
                        git.checkout_branch(Path(local_path), pr_branch)
                        ok = git.rebase_onto_origin(Path(local_path), base_branch)
                        if ok:
                            git.push_branch_force(Path(local_path), pr_branch)
                            rebased = True
                    except Exception:
                        pass

            if rebased:
                continue

            # Close the PR and requeue the task
            try:
                gh.post_comment(
                    owner, repo_name, pr_number,
                    f"{_STALE_PR_COMMENT_MARKER}\n\nThis PR has been open for more than "
                    f"{stale_days} days and could not be automatically rebased. "
                    "Closing and re-queuing the task for fresh execution.",
                )
                gh.close_pr(owner, repo_name, pr_number)
            except Exception as exc:
                _logger.info(json.dumps({
                    "event": "stale_pr_close_failed",
                    "pr_number": pr_number,
                    "error": str(exc),
                }))
                continue

            # Find matching Plane task via artifact pull_request_url
            if issues is None:
                issues = client.list_issues()
            for iss in issues:
                tid = str(iss.get("id", ""))
                artifact = service.usage_store.get_task_artifact(tid)
                if artifact and artifact.get("pull_request_url") == pr_url:
                    client.transition_issue(tid, "Backlog")
                    client.comment_issue(
                        tid,
                        render_worker_comment(
                            _STALE_PR_COMMENT_MARKER,
                            [
                                f"task_id: {tid}",
                                f"pr_url: {pr_url}",
                                f"pr_number: {pr_number}",
                                f"stale_days: {stale_days}",
                                "reason: PR closed after stale TTL; task re-queued to Backlog",
                            ],
                        ),
                    )
                    acted.append(tid)
                    break
    return acted


# ---------------------------------------------------------------------------
# Orphaned plane/ branch cleanup
# ---------------------------------------------------------------------------

def cleanup_orphaned_plane_branches(
    client: PlaneClient,
    service: "ExecutionService",
) -> list[str]:
    """Delete remote ``plane/<task-id>-*`` branches whose Plane task is Done/Cancelled.

    When kodo runs with ``push_on_validation_failure`` the system pushes a
    ``plane/<uuid>-<slug>`` branch as a draft review artifact.  These branches
    are never cleaned up automatically, accumulating as orphaned refs on GitHub.

    This function:
    1. Lists all remote branches matching ``plane/*`` for every repo that has
       a local checkout and a GitHub token.
    2. Extracts the task UUID from the branch name prefix.
    3. Fetches the Plane task state; if Done or Cancelled → deletes the branch.

    Returns a list of ``"<repo_key>/<branch>"`` strings for every deleted branch.
    """
    deleted: list[str] = []

    # Pre-build terminal status cache from a single list_issues call.
    try:
        all_issues = client.list_issues()
    except Exception:
        return deleted
    terminal_statuses: dict[str, str] = {}
    for _iss in all_issues:
        _s = issue_status_name(_iss).strip().lower()
        if _s in ("done", "cancelled"):
            terminal_statuses[str(_iss["id"])] = _s

    _UUID_PREFIX = re.compile(
        r"^plane/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        re.IGNORECASE,
    )

    for repo_key, repo_cfg in service.settings.repos.items():
        local_path = getattr(repo_cfg, "local_path", None)
        token = service.settings.repo_git_token(repo_key)
        if not local_path or not token:
            continue
        repo_path = Path(local_path)
        if not repo_path.exists():
            continue

        try:
            owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
            gh = GitHubPRClient(token)
        except Exception:
            continue

        # List remote plane/* branches via git ls-remote (fast, no full fetch needed).
        try:
            proc = subprocess.run(
                ["git", "ls-remote", "--heads", "origin", "plane/*"],
                capture_output=True, text=True, cwd=str(repo_path), timeout=30,
            )
            if proc.returncode != 0:
                continue
        except Exception:
            continue

        for line in proc.stdout.splitlines():
            # Format: "<sha>\trefs/heads/<branch>"
            parts = line.strip().split("\t", 1)
            if len(parts) != 2:
                continue
            ref = parts[1]
            branch = ref.removeprefix("refs/heads/")
            m = _UUID_PREFIX.match(branch)
            if not m:
                continue
            task_id = m.group(1)

            # Check if this task is terminal.
            status = terminal_statuses.get(task_id)
            if status is None:
                try:
                    iss = client.fetch_issue(task_id)
                    status = issue_status_name(iss).strip().lower()
                    if status in ("done", "cancelled"):
                        terminal_statuses[task_id] = status
                    else:
                        status = None
                except Exception:
                    pass

            if not status:
                continue  # Task is still active — keep the branch.

            # Delete the branch on GitHub.
            try:
                gh.delete_branch(owner, repo_name, branch)
                deleted.append(f"{repo_key}/{branch}")
                _logger.info(json.dumps({
                    "event": "orphaned_plane_branch_deleted",
                    "repo_key": repo_key,
                    "branch": branch,
                    "task_id": task_id,
                    "task_status": status,
                }))
            except Exception:
                pass

    return deleted


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
            # Re-blocked: the task was previously unblocked but the goal worker blocked it
            # again (e.g. retry cap hit after resolution). Escalate to create a re-triage
            # goal task, unless already escalated.
            if (
                blocked_issue_already_unblocked(client, task_id)
                and not blocked_issue_already_escalated(client, task_id)
            ):
                return task_id, "blocked_stale_escalation"
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

    # Sort by composite urgency score (highest first) so regression fixes and
    # high-priority tasks are claimed before routine maintenance work.
    sorted_issues = sorted(issues, key=issue_urgency_score, reverse=True)
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


def blocked_issue_already_unblocked(client: PlaneClient, task_id: str) -> bool:
    """Return True if this task was previously unblocked (unblock comment present).

    A task with an unblock comment that is still in Blocked state was re-blocked by
    the goal worker after a failed resolution attempt — it needs a new triage pass.
    """
    for comment in client.list_comments(task_id):
        if UNBLOCK_COMMENT_MARKER.lower() in extract_comment_text(comment).lower():
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


def recently_completed_proposal_keys(
    issues: list[dict[str, Any]],
    *,
    window_days: int = _DONE_PROPOSAL_DEDUP_WINDOW_DAYS,
    now: datetime | None = None,
) -> tuple[set[str], set[str]]:
    """Return (dedup_keys, title_tokens) for Done/Cancelled tasks completed recently.

    *dedup_keys* — normalised ``proposal_dedup_key`` values from recently
    completed tasks so the proposer can skip re-proposing identical work.

    *title_tokens* — normalised titles of recently completed tasks so a
    semantic-similarity check can catch near-duplicate titles even when the
    dedup key differs.

    Only tasks completed within *window_days* are included so suppression does
    not persist indefinitely for work that may be worth revisiting later.
    """
    _now = now or datetime.now(UTC)
    cutoff_ts = _now.timestamp() - window_days * 86400
    done_keys: set[str] = set()
    done_names: set[str] = set()

    for issue in issues:
        status = issue_status_name(issue).strip().lower()
        if status not in ("done", "cancelled"):
            continue
        # Age gate: use updated_at as a proxy for completion timestamp.
        updated_raw = issue.get("updated_at") or issue.get("created_at") or ""
        if updated_raw:
            try:
                updated_dt = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
                if updated_dt.tzinfo is None:
                    from datetime import timezone as _tz3
                    updated_dt = updated_dt.replace(tzinfo=_tz3.utc)
                if updated_dt.timestamp() < cutoff_ts:
                    continue  # Too old — allow re-proposal.
            except (ValueError, TypeError):
                pass

        description = issue_description_text(issue)
        key = parse_context_value(description, "proposal_dedup_key")
        if key:
            done_keys.add(key.strip().lower())
        name = str(issue.get("name", "")).strip().lower()
        if name:
            done_names.add(name)

    return done_keys, done_names


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


# Per-task-kind TTL (minutes) for reconcile_stale_running_issues.
# A Running task whose updated_at is within this window is skipped — it may
# still be legitimately running.  Longer for goal (complex refactors) than for
# test/improve (bounded verification passes).
_RUNNING_TTL_MINUTES: dict[str, int] = {
    "goal": 120,
    "test": 45,
    "improve": 30,
    "fix_pr": 45,
}
_RUNNING_TTL_DEFAULT_MINUTES = 90


def cleanup_orphaned_workspaces(prefix: str = "cp-task-") -> list[str]:
    """Delete /tmp/<prefix>* directories that no live process references.

    Reads /proc/*/cmdline to find all workspace paths currently in use, then
    removes any workspace directory that isn't mentioned by any process.
    Returns a list of deleted directory paths.
    """
    import glob as _glob

    workspaces = [Path(p) for p in _glob.glob(f"/tmp/{prefix}*") if Path(p).is_dir()]
    if not workspaces:
        return []

    # Collect every workspace tag that appears in any live process cmdline.
    live_tags: set[str] = set()
    try:
        for cmdline_path in Path("/proc").glob("*/cmdline"):
            try:
                text = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
                for ws in workspaces:
                    if ws.name in text:
                        live_tags.add(ws.name)
            except OSError:
                pass
    except OSError:
        pass

    deleted: list[str] = []
    for ws in workspaces:
        if ws.name in live_tags:
            continue
        try:
            shutil.rmtree(ws)
            deleted.append(str(ws))
        except OSError:
            pass
    return deleted


def reconcile_stale_running_issues(
    client: PlaneClient,
    *,
    role: str,
    ready_state: str,
    usage_store: "UsageStore | None" = None,
    startup: bool = False,
) -> list[str]:
    """Reconcile tasks stuck in Running state after a worker restart or mid-run kill.

    On startup (startup=True) a short 15-minute TTL is applied so tasks claimed
    by the previous worker session (whose kodo process is also dead) are reclaimed
    immediately rather than sitting in Running for up to 2 hours.
    """
    if role not in {"goal", "test", "improve"}:
        return []
    store = usage_store or UsageStore()
    usage_data = store.load()
    task_attempts: dict[str, int] = {
        k: int(v) for k, v in usage_data.get("task_attempts", {}).items()
    }
    task_signatures: dict[str, str | None] = usage_data.get("last_task_signatures", {})
    reconciled: list[str] = []
    _now = datetime.now(UTC)
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
        # TTL guard: skip tasks that were updated recently — they may still be
        # legitimately running.  Uses updated_at (set when the task moved to
        # Running) with a per-kind time-to-live.
        _updated_raw = issue.get("updated_at") or issue.get("created_at")
        if _updated_raw:
            try:
                _updated = datetime.fromisoformat(str(_updated_raw).replace("Z", "+00:00"))
                if _updated.tzinfo is None:
                    _updated = _updated.replace(tzinfo=UTC)
                _ttl = (15 if startup else _RUNNING_TTL_MINUTES.get(task_kind, _RUNNING_TTL_DEFAULT_MINUTES)) * 60
                if (_now - _updated).total_seconds() < _ttl:
                    continue  # Within TTL — do not touch
            except (ValueError, TypeError):
                pass  # Unparseable timestamp; fall through to existing logic
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

    # S10-2: awaiting_input must be checked first — it is a positive signal that
    # the agent was working and stopped to ask a question, not an error.
    if "blocked_classification: awaiting_input" in lowered or "<!-- cp:question:" in lowered:
        return "awaiting_input", "Kodo stopped to ask a clarifying question — provide an answer to resume."
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
    if "blocked_classification: flaky_test" in lowered:
        return "flaky_test", "The validation command is intermittently failing — the test itself needs to be stabilized."
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
    usage_store: "UsageStore | None" = None,
) -> ImproveTriageResult:
    classification, rationale = classify_blocked_issue(issue, comments)
    classification_counts = recent_classification_counts(client)
    # Only escalate to a system-fix follow-up task once the same classification
    # has appeared at least 3 times in recent issues, giving retries time to work.
    repeated_pattern = classification_counts.get(classification, 0) >= 3
    issue_title = str(issue.get("name", "task"))
    issue_kind = task_kind_for_issue(issue)

    # S10-2: awaiting_input — Kodo asked a question mid-execution.  Surface the
    # question as a human-attention comment; when the human replies the improve
    # watcher will detect the answer and re-run the original task.
    if classification == "awaiting_input":
        # Try to recover the question text from the latest worker comment.
        # Check all raw comment fields because extract_comment_text strips HTML comments.
        question_text = ""
        for c in reversed(comments):
            for field in ("comment_stripped", "comment", "body", "comment_html"):
                raw = str(c.get(field) or "")
                m = _CP_QUESTION_RE.search(raw)
                if m:
                    question_text = m.group(1).strip()
                    break
            if question_text:
                break
        reason = (
            f"Kodo needs clarification before it can proceed with '{issue_title}'."
        )
        if question_text:
            reason += f"\n\n**Question from Kodo:** {question_text}"
        reason += (
            "\n\nReply to this comment with an answer.  "
            "The improve watcher will detect your response and re-run the task "
            "with the answer injected into the goal description."
        )
        return ImproveTriageResult(
            classification=classification,
            certainty="high",
            reason_summary=reason,
            recommended_action="human_attention",
            human_attention_required=True,
        )

    if classification == "infra_tooling":
        # Transient infra/tooling failures (network blip, provider restart) are
        # auto-retried rather than requiring human attention.  The task is moved
        # back to Ready for AI by handle_blocked_triage so it is retried on the
        # next goal-watcher cycle.
        return ImproveTriageResult(
            classification=classification,
            certainty="high",
            reason_summary=rationale,
            recommended_action="retry",
            human_attention_required=False,
        )
    if classification == "parse_config":
        # The task description or repo config is structurally invalid — kodo
        # cannot execute it and retrying will never help.  Creating a
        # "Resolve blocked" follow-up would also fail for the same reason.
        # Cancel the task so the board stays clean and the proposer can
        # eventually re-propose a correctly-formed replacement.
        return ImproveTriageResult(
            classification=classification,
            certainty="high",
            reason_summary=(
                f"{rationale} This is a structural description or configuration problem "
                "that cannot be fixed by re-running the task — cancelling to keep the board clean."
            ),
            recommended_action="cancel",
            human_attention_required=False,
        )
    if classification == UNKNOWN_BLOCKED_CLASSIFICATION:
        return ImproveTriageResult(
            classification=classification,
            certainty="low",
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
        # Inject prior progress so the follow-up doesn't start from scratch
        _store = usage_store or UsageStore()
        prior_artifact = _store.get_task_artifact(str(issue.get("id", "")))
        if prior_artifact:
            raw_summary = str(prior_artifact.get("summary") or "").strip()
            if raw_summary:
                truncated = raw_summary[:800] + ("\u2026" if len(raw_summary) > 800 else "")
                indented = truncated.replace("\n", "\n  ")
                goal_text += f"\n\nprior_progress: |\n  {indented}"
    if classification == "dependency_missing":
        goal_text = (
            f"The task '{issue_title}' failed because a required dependency was not available. "
            "Install or configure the missing dependency in the repo bootstrap, then re-attempt the original task."
        )
    if classification == "flaky_test":
        goal_text = (
            f"The task '{issue_title}' failed due to an intermittently-failing test. "
            "Identify the flaky test command, diagnose the root cause (race condition, external dependency, "
            "timing issue), and stabilize it — either by fixing the test or marking it appropriately."
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
        task_artifact = (usage_store or UsageStore()).get_task_artifact(str(issue.get("id", "")))
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
            if repo_key in service.settings.repos:
                # Fall back to repo default_branch when the header omits base_branch
                # (e.g. regression tasks and re-triage tasks only specify repo: and mode:).
                if not base_branch:
                    base_branch = service.settings.repos[repo_key].default_branch
                allowed_paths = [str(path).strip() for path in metadata.get("allowed_paths", []) if str(path).strip()]
                return repo_key, base_branch, allowed_paths or allowed_paths_for_repo(repo_key)
        except ValueError as exc:
            _logger.debug("Failed to parse task metadata: %s", exc)

    repo_key = default_repo_key(service)
    repo_cfg = service.settings.repos[repo_key]
    return repo_key, repo_cfg.default_branch, allowed_paths_for_repo(repo_key)


def existing_issue_names(
    client: PlaneClient,
    *,
    issues: list[dict[str, Any]] | None = None,
    include_terminal: bool = False,
) -> set[str]:
    """Return the lowercased set of existing issue names.

    By default Done/Cancelled issues are excluded so that proposers don't
    treat a completed task as a duplicate of a new (different) proposal.
    Pass ``include_terminal=True`` for deduplication checks that *should*
    treat a previously-completed task title as already covered — used by
    the dependency update scanner to prevent re-creating tasks whose exact
    version bump has already been Done.
    """
    names: set[str] = set()
    for issue in issues or client.list_issues():
        if not include_terminal:
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
    _fu_labels = [f"task-kind: {task_kind}", f"source: {source_role}-worker"]
    # Propagate repo: label so the goal/test watcher can filter by repo correctly.
    for _lbl in issue_label_names(original_issue):
        if _lbl.lower().startswith("repo:"):
            _fu_labels.append(_lbl)
            break
    if _self_modify_approved(original_issue):
        _fu_labels.append("self-modify: approved")
    created = client.create_issue(
        name=title,
        description=description,
        state=state,
        label_names=_fu_labels,
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


_COMPLEXITY_FILE_THRESHOLD_HIGH = 8   # ≥8 affected files → "high" complexity
_COMPLEXITY_FILE_THRESHOLD_MEDIUM = 3  # 3–7 files → "medium"


def _estimate_task_complexity(proposal: "ProposalSpec") -> str:
    """Estimate the implementation complexity of *proposal*.

    Uses a simple heuristic:
    - Count the number of distinct file paths mentioned in evidence_lines.
    - Return ``"high"`` when ≥ 8 files, ``"medium"`` when 3–7, ``"low"`` otherwise.

    This is intentionally conservative: it only suppresses tasks where many
    files are clearly needed, not when the scope is merely ambiguous.
    """
    evidence_files = _extract_evidence_file_tokens(getattr(proposal, "evidence_lines", []))
    # Also count explicit file references in the goal text
    goal_files = _extract_filename_tokens(proposal.goal_text or "")
    total_files = len(evidence_files | goal_files)
    if total_files >= _COMPLEXITY_FILE_THRESHOLD_HIGH:
        return "high"
    if total_files >= _COMPLEXITY_FILE_THRESHOLD_MEDIUM:
        return "medium"
    return "low"


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

    # Success/failure learning: boost high-success categories to Ready for AI;
    # demote low-success categories to Backlog.
    _now_sr = datetime.now(UTC)
    for proposal in proposals:
        rate = service.usage_store.proposal_success_rate(proposal.task_kind, now=_now_sr)
        if rate > 0.7 and proposal.recommended_state == "Backlog":
            proposal.recommended_state = "Ready for AI"
            notes.append(f"success_boost: {proposal.task_kind} rate={rate:.2f}")
        elif rate < 0.3 and proposal.recommended_state == "Ready for AI":
            proposal.recommended_state = "Backlog"
            notes.append(f"success_demotion: {proposal.task_kind} rate={rate:.2f}")

    # Point 5: Self-repo proposals always go to Backlog — they need explicit
    # "self-modify: approved" before the goal watcher will auto-execute them.
    if _is_self_repo(repo_key, service):
        for proposal in proposals:
            if proposal.recommended_state == "Ready for AI":
                proposal.recommended_state = "Backlog"
        notes.append("self_repo_gate: self-repo proposals capped at Backlog (require self-modify:approved label)")

    # S10-6: Complexity gate — suppress proposals whose scope clearly exceeds
    # what kodo can handle in one run.  High-complexity proposals are moved to
    # Backlog (not suppressed entirely) so a human or multi-step plan can pick
    # them up without the system repeatedly re-proposing them.
    complexity_capped = 0
    for proposal in proposals:
        if proposal.recommended_state == "Backlog":
            continue  # Already demoted; no need to re-evaluate
        complexity = _estimate_task_complexity(proposal)
        if complexity == "high":
            proposal.recommended_state = "Backlog"
            if proposal.confidence == "high":
                proposal.confidence = "medium"
            complexity_capped += 1
    if complexity_capped:
        notes.append(f"complexity_gate: capped {complexity_capped} high-complexity proposal(s) to Backlog")

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
    # S10-1: Inject historical rejection patterns so Kodo knows what reviewers
    # have flagged before for this family/repo combination.
    _rej_patterns = _load_rejection_patterns_for_proposal(
        family=proposal.task_kind, repo_key=proposal.repo_key or default_repo_key(service)
    )
    if _rej_patterns:
        lines.extend(["", "## Prior Rejection Patterns",
                       "Reviewers have previously flagged these concerns for similar tasks — "
                       "proactively address them:"])
        lines.extend(f"- {p}" for p in _rej_patterns)
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
    done_keys: set[str] | None = None,
    done_names: set[str] | None = None,
) -> dict[str, Any] | None:
    normalized_title = proposal.title.strip().lower()
    normalized_key = proposal.dedup_key.strip().lower()
    if normalized_title in existing_names or normalized_key in proposal_keys:
        return None
    # S8-3b: Semantic near-duplicate check — suppress proposals whose title is
    # highly similar to an existing board task even if the wording differs.
    _new_files = _extract_filename_tokens(normalized_title)
    for existing_name in existing_names:
        if _semantic_title_similarity(normalized_title, existing_name) >= _SEMANTIC_DEDUP_THRESHOLD:
            # If both titles name specific .py files but those files don't overlap,
            # they target different code — not a duplicate (e.g. "Decompose main.py"
            # must not be suppressed by "Decompose stage_driver.py").
            _existing_files = _extract_filename_tokens(existing_name)
            if _new_files and _existing_files and not (_new_files & _existing_files):
                continue
            _logger.info(json.dumps({
                "event": "propose_semantic_dedup_suppressed",
                "title": proposal.title,
                "similar_to": existing_name,
            }))
            return None
    if memory is not None and now is not None:
        if recently_proposed(memory, title=proposal.title, dedup_key=proposal.dedup_key, now=now):
            return None
    # Dedup against recently completed (Done/Cancelled) work so the proposer does
    # not recreate tasks for work that was just finished.
    if done_keys and normalized_key in done_keys:
        _logger.info(json.dumps({
            "event": "propose_done_dedup_suppressed",
            "title": proposal.title,
            "dedup_key": proposal.dedup_key,
            "reason": "matching dedup_key found in recently completed tasks",
        }))
        return None
    if done_names:
        for done_name in done_names:
            if _semantic_title_similarity(normalized_title, done_name) >= _SEMANTIC_DEDUP_THRESHOLD:
                _done_files = _extract_filename_tokens(done_name)
                if _new_files and _done_files and not (_new_files & _done_files):
                    continue
                _logger.info(json.dumps({
                    "event": "propose_done_dedup_suppressed",
                    "title": proposal.title,
                    "similar_to": done_name,
                    "reason": "semantically similar title found in recently completed tasks",
                }))
                return None

    description = build_proposal_description(service=service, proposal=proposal)
    reason_label = re.sub(r"[^a-z0-9_]+", "_", proposal.source_signal.lower()).strip("_")
    _prop_labels = [f"task-kind: {proposal.task_kind}", "source: proposer", f"reason: {reason_label}"]
    if proposal.repo_key:
        _prop_labels.append(f"repo: {proposal.repo_key}")
    _prop_repo_key = proposal.repo_key or default_repo_key(service)
    if _is_self_repo(_prop_repo_key, service):
        _prop_labels.append("self-modify: approved")
    created = client.create_issue(
        name=proposal.title,
        description=description,
        state=proposal.recommended_state,
        label_names=_prop_labels,
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


def _scheduled_tasks_due(
    scheduled: list[Any],
    existing_names: set[str],
    *,
    now: datetime,
    lookback_seconds: int = 120,
) -> list[Any]:
    """Return scheduled tasks whose cron expression fired within *lookback_seconds* of *now*.

    Requires ``croniter``; silently returns [] if not installed.
    """
    from datetime import timedelta

    try:
        from croniter import croniter  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]
    except ImportError:
        return []

    due = []
    for st in scheduled:
        try:
            it = croniter(st.cron, now - timedelta(seconds=lookback_seconds))
            next_ts = it.get_next(datetime)
            if next_ts <= now:
                if st.title.strip().lower() not in existing_names:
                    due.append(st)
        except Exception:
            pass
    return due


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


def _score_proposal_utility(
    proposal: "ProposalSpec",
    *,
    now: datetime | None = None,
) -> float:
    """Return a utility score for *proposal* used to rank candidates before the cycle cap.

    Higher score → higher priority within a cycle.  The score is the sum of:

    - **Confidence weight** (high=1.0, medium=0.6, low=0.2)
    - **Calibration bonus** (0–0.4): observed acceptance rate from
      ``ConfidenceCalibrationStore`` when ≥5 records exist, otherwise 0.
    - **Scope penalty** (-0.05 per affected file beyond 2, capped at -0.3):
      proposals touching fewer files are cheaper to execute correctly.
    - **State bonus** (0.2 when ``recommended_state == "Ready for AI"``): prefer
      proposals that are immediately actionable.

    The score is intentionally simple — it is meant to break ties, not to
    be a rigorous ranking.
    """
    _now = now or datetime.now(UTC)
    conf_map = {"high": 1.0, "medium": 0.6, "low": 0.2}
    score = conf_map.get(proposal.confidence, 0.3)

    # Calibration bonus
    try:
        from control_plane.tuning.calibration import ConfidenceCalibrationStore
        _calib = ConfidenceCalibrationStore()
        rate = _calib.calibration_for(proposal.task_kind, proposal.confidence, repo_key=proposal.repo_key or None)
        if rate is not None:
            score += rate * 0.4  # scale acceptance rate [0,1] → bonus [0,0.4]
    except Exception:
        pass

    # Scope penalty
    evidence_files = _extract_evidence_file_tokens(getattr(proposal, "evidence_lines", []))
    extra_files = max(0, len(evidence_files) - 2)
    score -= min(extra_files * 0.05, 0.3)

    # State bonus
    if proposal.recommended_state == "Ready for AI":
        score += 0.2

    return score


def _decision_artifacts_stale(stale_hours: int = _AUTONOMY_ARTIFACT_STALE_HOURS) -> bool:
    """Return True if no decision artifact has been written within *stale_hours*."""
    decision_root = Path("tools/report/control_plane/decision")
    if not decision_root.exists():
        return True
    run_dirs = sorted(
        (p for p in decision_root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not run_dirs:
        return True
    age_hours = (datetime.now(UTC).timestamp() - run_dirs[0].stat().st_mtime) / 3600
    return age_hours >= stale_hours


def _trigger_autonomy_refresh(settings: Any, client: PlaneClient) -> None:
    """Run the observe→insights→decide→propose pipeline inline.

    Called by the propose watcher when the board drains and decision artifacts
    are stale.  Replaces the removed cron job.  Errors are logged but never
    propagated so the propose cycle always continues normally.
    """
    _ac_logger = logging.getLogger(__name__)
    _ac_logger.info(json.dumps({"event": "propose_autonomy_refresh_start"}))
    try:
        from control_plane.entrypoints.autonomy_cycle.main import run_pipeline
        result = run_pipeline(settings, client)
        _ac_logger.info(json.dumps({
            "event": "propose_autonomy_refresh_done",
            "created": result.get("created", 0),
            "skipped": result.get("skipped", 0),
            "failed": result.get("failed", 0),
            "error": result.get("error"),
        }))
    except Exception as exc:
        _ac_logger.error(json.dumps({"event": "propose_autonomy_refresh_error", "error": str(exc)}))


def handle_propose_cycle(
    client: PlaneClient,
    service: ExecutionService,
    *,
    status_dir: Path | None = None,
    now: datetime | None = None,
) -> ProposalCycleResult:
    now = now or datetime.now(UTC)
    issues = client.list_issues()

    # Label-repair pass: tasks whose description contains a "repo:" execution header
    # but are missing the matching label are invisible to promote_backlog_tasks and the
    # goal worker.  Detect and patch them so they re-enter the normal flow instead of
    # silently stagnating forever.
    _repair_logger = logging.getLogger(__name__)
    _LIVE_STATES = {"backlog", "ready for ai", "blocked", "running", "todo", "in progress"}
    for _iss in issues:
        if issue_status_name(_iss).strip().lower() not in _LIVE_STATES:
            continue
        _existing_labels = issue_label_names(_iss)
        if any(lbl.lower().startswith("repo:") for lbl in _existing_labels):
            continue  # already labelled
        # Try to infer repo from the task description metadata.
        try:
            _desc_repo_key, _, _ = issue_execution_target(_iss, service)
        except Exception:
            continue
        if not _desc_repo_key or _desc_repo_key not in service.settings.repos:
            continue
        _new_labels = list(_existing_labels) + [f"repo: {_desc_repo_key}"]
        _new_labels_lower = [lbl.lower() for lbl in _new_labels]
        if "task-kind: goal" not in _new_labels_lower and "task-kind: test" not in _new_labels_lower:
            _new_labels.append("task-kind: goal")
        if not any("source:" in lbl.lower() for lbl in _new_labels):
            _new_labels.append("source: proposer")
        if _is_self_repo(_desc_repo_key, service) and "self-modify: approved" not in _new_labels:
            _new_labels.append("self-modify: approved")
        try:
            client.update_issue_labels(str(_iss["id"]), _new_labels)
            _repair_logger.info(json.dumps({
                "event": "propose_label_repair",
                "task_id": str(_iss["id"]),
                "repo_key": _desc_repo_key,
                "labels_added": _new_labels,
            }))
            # Refresh the issue list so downstream logic sees the patched labels.
            issues = client.list_issues()
            break  # repair one per cycle to avoid thrashing; next cycle picks up more
        except Exception as _exc:
            _repair_logger.warning(json.dumps({
                "event": "propose_label_repair_failed",
                "task_id": str(_iss["id"]),
                "error": str(_exc),
            }))

    # Scheduled tasks: create any that are due and not already on the board
    scheduled = list(getattr(service.settings, "scheduled_tasks", None) or [])
    if scheduled:
        _sch_names = existing_issue_names(client, issues=issues)
        _due = _scheduled_tasks_due(scheduled, _sch_names, now=now)
        _sch_created: list[str] = []
        for st in _due:
            repo_cfg = service.settings.repos.get(st.repo_key)
            if repo_cfg is None:
                continue
            new_t = client.create_issue(
                name=st.title,
                description=(
                    f"## Execution\nrepo: {st.repo_key}\nmode: goal\n\n"
                    f"## Goal\n{st.goal}\n\n"
                    "## Context\n"
                    f"- source: scheduled_task\n"
                    f"- cron: {st.cron}\n"
                ),
                state="Ready for AI",
                label_names=[f"task-kind: {st.kind}", f"repo: {st.repo_key}", "source: scheduler"]
                + (["self-modify: approved"] if _is_self_repo(st.repo_key, service) else []),
            )
            _sch_created.append(str(new_t.get("id", "")))
        if _sch_created:
            return ProposalCycleResult(
                created_task_ids=_sch_created,
                decision="scheduled_tasks_created",
                board_idle=False,
                reason_summary=f"Created {len(_sch_created)} scheduled task(s).",
                proposed_state="Ready for AI",
            )

    board_idle = board_is_idle_for_proposals_from_issues(issues)

    # Board saturation: don't add more autonomy tasks if the queue is already
    # large.  This prevents a burst autonomy-cycle run from flooding the board
    # faster than the watcher lanes can drain it.
    _autonomy_queued = sum(
        1 for _iss in issues
        if issue_status_name(_iss) in ("Ready for AI", "Backlog")
        and any("source: autonomy" == lbl.strip().lower() for lbl in issue_label_names(_iss))
    )
    if _autonomy_queued >= MAX_QUEUED_AUTONOMY_TASKS:
        return ProposalCycleResult(
            created_task_ids=[],
            decision="board_saturated",
            board_idle=False,
            reason_summary=(
                f"Proposal creation suppressed: {_autonomy_queued} autonomy tasks already "
                f"queued (limit {MAX_QUEUED_AUTONOMY_TASKS}). "
                "Drain the queue before creating more."
            ),
        )

    # Promote existing Backlog tasks whenever the board has capacity.  Previously
    # this only fired when active_count == 0, which caused a deadlock with
    # multi-step-plan sub-tasks: after step 1 runs, the board still has other
    # RFA tasks, so active_count > 0 → promotion never fires → steps 2/3 stuck
    # in Backlog indefinitely.  We now promote up to the congestion limit so
    # queued Backlog tasks (proposer, multi-step-plan, etc.) flow through as
    # capacity opens up.
    active_count = active_task_count_from_issues(issues, service=service)
    _promotion_slots = max(0, MAX_ACTIVE_TASKS_FOR_PROPOSALS - active_count)
    if _promotion_slots > 0:
        promoted_ids = promote_backlog_tasks(
            client, issues, max_promotions=_promotion_slots, service=service
        )
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
    # Exception: when the board is fully drained (active_count == 0), reset the
    # satiation window and refresh the decision artifacts inline so the proposer
    # has fresh candidates rather than waiting for an external trigger.
    if active_count == 0:
        service.usage_store.reset_satiation_window(now=now)
        if _decision_artifacts_stale():
            _trigger_autonomy_refresh(service.settings, client)
    elif service.usage_store.is_proposal_satiated(now=now):
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

    # S10-7: Sort proposals by utility score before applying the cycle cap so the
    # highest-value proposals are created first within each cycle.
    proposals.sort(key=lambda p: _score_proposal_utility(p, now=now), reverse=True)

    existing_names = existing_issue_names(client, issues=issues)
    proposal_keys = existing_proposal_keys(client, issues=issues)
    done_keys, done_names = recently_completed_proposal_keys(issues, now=now)
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
        # S9-9: Skip proposals that conflict with an in-flight task.
        # Uses evidence_lines for higher-fidelity file detection than title tokens alone.
        evidence_files = _extract_evidence_file_tokens(getattr(proposal, "evidence_lines", []))
        _conflict_title_check = _has_conflict_with_active_task(proposal.title, issues, service.usage_store, open_pr_files)
        _conflict_evidence_check = bool(evidence_files) and _has_conflict_with_active_task(
            " ".join(evidence_files), issues, service.usage_store, open_pr_files
        )
        if _conflict_title_check or _conflict_evidence_check:
            _logger.info(json.dumps({
                "event": "propose_conflict_skipped",
                "title": proposal.title,
                "open_pr_files_count": len(open_pr_files),
                "evidence_files_checked": len(evidence_files),
            }))
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
            done_keys=done_keys,
            done_names=done_names,
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

    # Multi-step planning: decompose complex tasks before executing
    plan_ids = build_multi_step_plan(client, service, task_id, issue)
    if plan_ids:
        return plan_ids

    selected_evidence = selected_evidence_from_issue(issue)
    target_area_hint = target_area_hint_from_issue(issue)
    _goal_t0 = time.monotonic()
    result = run_service_task(service, client, task_id, worker_role="goal")
    _goal_duration = time.monotonic() - _goal_t0
    # S6-5: Record execution duration and flag anomalies.
    try:
        service.usage_store.record_execution_duration(task_id=task_id, role="goal", duration_seconds=_goal_duration, now=datetime.now(UTC))
        _median_goal = service.usage_store.median_execution_duration("goal")
        if _median_goal is not None and _goal_duration > _median_goal * 2:
            logging.getLogger(__name__).warning(json.dumps({
                "event": "duration_anomaly",
                "role": "goal",
                "task_id": task_id,
                "duration_seconds": round(_goal_duration, 1),
                "median_seconds": round(_median_goal, 1),
            }))
    except Exception:
        pass
    created_ids: list[str] = []
    if result.outcome_status == "skipped":
        # Circuit-breaker skip: service returned early without transitioning the task.
        # Move it back to Ready for AI so it will be retried once the blocker clears.
        client.comment_issue(
            task_id,
            render_worker_comment(
                "[Goal] Execution skipped; re-queued",
                [
                    f"run_id: {result.run_id}",
                    f"task_id: {task_id}",
                    "task_kind: goal",
                    f"outcome_reason: {result.outcome_reason or 'skipped'}",
                    "next_action: task re-queued to Ready for AI; retry will happen once the blocker is resolved",
                ],
            ),
        )
        client.transition_issue(task_id, "Ready for AI")
        if result.outcome_reason == "orchestrator_rate_limited":
            # Kodo hit a Claude Code usage limit.  Sleep before returning so the
            # watcher doesn't immediately re-pick the task and hammer the limit.
            time.sleep(ORCHESTRATOR_RATE_LIMIT_BACKOFF_SECONDS)
        return created_ids
    if result.outcome_status == "no_op":
        if not result.validation_passed:
            # No changes made but validation fails → pre-existing failure.
            # Service already set status=Cancelled and created a fix-validation task.
            _noop_fix_ids = result.follow_up_task_ids or []
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Goal] No repo change produced; pre-existing validation failure detected",
                    [
                        f"run_id: {result.run_id}",
                        f"task_id: {task_id}",
                        "task_kind: goal",
                        "result_status: cancelled",
                        f"outcome_reason: {result.outcome_reason or 'no_op'}",
                        f"selected_evidence: {selected_evidence}",
                        f"target_area_hint: {target_area_hint}",
                        "blocked_classification: pre_existing_validation",
                        f"follow_up_task_ids: {', '.join(_noop_fix_ids) if _noop_fix_ids else 'none'}",
                        "next_action: fix-validation task created; this task cancelled to break triage loop",
                    ],
                ),
            )
        else:
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Goal] No meaningful repo change produced",
                    [
                        f"run_id: {result.run_id}",
                        f"task_id: {task_id}",
                        "task_kind: goal",
                        "result_status: done",
                        f"outcome_reason: {result.outcome_reason or 'no_op'}",
                        f"selected_evidence: {selected_evidence}",
                        f"target_area_hint: {target_area_hint}",
                        "bounded_scope_reason: execution produced no meaningful repo change to verify",
                        "follow_up_task_ids: none",
                        "next_action: no change needed; task marked done",
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
                "[Goal] Execution blocked by environment/auth — auto-retrying",
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
                    "next_action: task re-queued to Ready for AI; will retry automatically once tooling is available",
                ],
            ),
        )
        rewrite_worker_summary(result, service, task_id)
        # Auto-requeue: transition back to RFA so a transient infra hiccup
        # (network blip, provider restart) recovers without human intervention.
        client.transition_issue(task_id, "Ready for AI")
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
        # Record validation outcomes for flaky-test detection
        _now_vr = datetime.now(UTC)
        for vr in result.validation_results:
            service.usage_store.record_validation_outcome(
                command=vr.command, passed=(vr.exit_code == 0), now=_now_vr
            )
        result.blocked_classification = classify_execution_result(result, service.usage_store)
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
    # Record success/failure for proposal-category learning
    try:
        category = next(
            (lbl.split(":", 1)[1].strip()
             for lbl in issue_label_names(issue)
             if lbl.lower().startswith("task-kind:")),
            "goal",
        )
        service.usage_store.record_proposal_outcome(
            category=category,
            succeeded=result.success,
            now=datetime.now(UTC),
        )
    except Exception:
        pass
    # Circuit-breaker outcome recording.
    # Quota exhaustion is an infrastructure failure — do NOT drain the circuit
    # breaker window (it would block all tasks until the operator investigates,
    # without the circuit-breaker being the right signal).
    # Fix-validation tasks are created to fix pre-existing failures and are
    # expected to fail or timeout occasionally — their outcomes should not count
    # against the circuit breaker, which tracks regular task quality.
    _is_fix_validation = str(issue.get("name", "")).startswith("Fix pre-existing validation failure in")
    try:
        if _is_quota_exhausted_result(result):
            service.usage_store.record_kodo_quota_event(
                task_id=task_id, role="goal", now=datetime.now(UTC)
            )
        elif not _is_fix_validation:
            service.usage_store.record_execution_outcome(
                task_id=task_id,
                role="goal",
                succeeded=result.success,
                now=datetime.now(UTC),
                kodo_version=_get_kodo_version(service.settings.kodo.binary),
            )
    except Exception:
        pass
    # Cost telemetry
    try:
        _cost = float(getattr(service.settings, "cost_per_execution_usd", 0.0))
        if _cost > 0:
            _repo_key = _extract_repo_key(issue, service)
            service.usage_store.record_execution_cost(
                task_id=task_id,
                repo_key=_repo_key,
                estimated_usd=_cost,
                now=datetime.now(UTC),
            )
    except Exception:
        pass
    # S7-6: Cross-repo impact analysis.
    # When changed files overlap with shared interface paths declared in any other
    # repo's impact_report_paths, annotate the task with a cross-repo warning.
    if result.success and result.changed_files:
        try:
            _cross_repo_warnings = _check_cross_repo_impact(result.changed_files, service)
            if _cross_repo_warnings:
                client.comment_issue(
                    task_id,
                    render_worker_comment(
                        "[Goal] Cross-repo impact detected",
                        [
                            f"task_id: {task_id}",
                            "action: changed files overlap with shared interface paths",
                            *[f"impact: {w}" for w in _cross_repo_warnings],
                            "recommendation: verify dependent repos still build and pass tests",
                        ],
                    ),
                )
                _logger.warning(json.dumps({
                    "event": "cross_repo_impact_detected",
                    "task_id": task_id,
                    "warnings": _cross_repo_warnings,
                }))
        except Exception:
            pass
    rewrite_worker_summary(result, service, task_id)
    return created_ids


def _check_cross_repo_impact(
    changed_files: list[str],
    service: "ExecutionService",
) -> list[str]:
    """Return warning strings when *changed_files* touch shared interface paths.

    Checks ``impact_report_paths`` declared on every repo in settings.  Each
    entry is treated as a path prefix; a match is reported as a warning string
    describing which repo and which shared path was touched.
    """
    warnings: list[str] = []
    for repo_key, repo_cfg in service.settings.repos.items():
        impact_paths = list(getattr(repo_cfg, "impact_report_paths", []) or [])
        if not impact_paths:
            continue
        for changed in changed_files:
            for shared_path in impact_paths:
                if changed.startswith(shared_path.rstrip("/")):
                    warnings.append(
                        f"repo={repo_key} shared_path={shared_path} changed_file={changed}"
                    )
    return warnings


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


# Strings that indicate a hard account-level quota exhaustion in kodo stderr.
# These parallel KodoAdapter._HARD_QUOTA_EXHAUSTED_SIGNALS but are checked on
# the ExecutionResult.execution_stderr_excerpt (already extracted by service.py).
_QUOTA_EXHAUSTED_EXCERPT_SIGNALS = (
    "insufficient_quota",
    "you've exceeded your usage limit",
    "you have exceeded your usage limit",
    "you have run out of credits",
    "upgrade your plan",
    "payment required",
)


def _is_quota_exhausted_result(result: ExecutionResult) -> bool:
    """Return True when the execution result signals a hard quota exhaustion."""
    excerpt = (result.execution_stderr_excerpt or "").lower()
    return any(s in excerpt for s in _QUOTA_EXHAUSTED_EXCERPT_SIGNALS)


def handle_test_task(client: PlaneClient, service: ExecutionService, task_id: str) -> list[str]:
    issue = client.fetch_issue(task_id)
    selected_evidence = selected_evidence_from_issue(issue)
    target_area_hint = target_area_hint_from_issue(issue)
    _test_t0 = time.monotonic()
    result = run_service_task(service, client, task_id, worker_role="test")
    _test_duration = time.monotonic() - _test_t0
    # S6-5: Record execution duration and flag anomalies.
    try:
        service.usage_store.record_execution_duration(task_id=task_id, role="test", duration_seconds=_test_duration, now=datetime.now(UTC))
        _median_test = service.usage_store.median_execution_duration("test")
        if _median_test is not None and _test_duration > _median_test * 2:
            logging.getLogger(__name__).warning(json.dumps({
                "event": "duration_anomaly",
                "role": "test",
                "task_id": task_id,
                "duration_seconds": round(_test_duration, 1),
                "median_seconds": round(_median_test, 1),
            }))
    except Exception:
        pass
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

    # Record validation outcomes for flaky-test detection
    _now_vr2 = datetime.now(UTC)
    for vr in result.validation_results:
        service.usage_store.record_validation_outcome(
            command=vr.command, passed=(vr.exit_code == 0), now=_now_vr2
        )
    blocked_classification = classify_execution_result(result, service.usage_store)
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
    # Circuit-breaker outcome recording (skip for quota exhaustion — infra failure).
    try:
        if _is_quota_exhausted_result(result):
            service.usage_store.record_kodo_quota_event(
                task_id=task_id, role="test", now=datetime.now(UTC)
            )
        else:
            service.usage_store.record_execution_outcome(
                task_id=task_id,
                role="test",
                succeeded=result.success,
                now=datetime.now(UTC),
                kodo_version=_get_kodo_version(service.settings.kodo.binary),
            )
    except Exception:
        pass
    # Cost telemetry
    try:
        _cost = float(getattr(service.settings, "cost_per_execution_usd", 0.0))
        if _cost > 0:
            _issue_t = client.fetch_issue(task_id)
            _repo_key_t = _extract_repo_key(_issue_t, service)
            service.usage_store.record_execution_cost(
                task_id=task_id,
                repo_key=_repo_key_t,
                estimated_usd=_cost,
                now=datetime.now(UTC),
            )
    except Exception:
        pass
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
    triage = build_improve_triage_result(client, issue, comments, usage_store=service.usage_store)
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

    _triage_result_status = "cancelled" if triage.recommended_action == "cancel" else "blocked"
    client.comment_issue(
        task_id,
        render_worker_comment(
            TRIAGE_COMMENT_MARKER,
            [
                f"task_id: {task_id}",
                f"task_kind: {task_kind_for_issue(issue)}",
                f"result_status: {_triage_result_status}",
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
    if triage.recommended_action == "retry":
        # Auto-retry path (e.g. infra_tooling): move back to Ready for AI so
        # the goal watcher picks it up again without manual operator action.
        client.transition_issue(task_id, "Ready for AI")
    elif triage.recommended_action == "cancel":
        # Structural problem (parse_config): no retry will help; cancel so the
        # board stays clean and the proposer can re-create a valid replacement.
        client.transition_issue(task_id, "Cancelled")
    else:
        client.transition_issue(task_id, "Blocked")
    # Record triage event for escalation tracking
    service.usage_store.record_blocked_triage(
        task_id=task_id,
        classification=triage.classification,
        now=datetime.now(UTC),
    )
    if triage.human_attention_required:
        notify_human_attention(
            task_id=task_id,
            task_title=str(issue.get("name", "")),
            classification=triage.classification,
            reason=triage.reason_summary,
        )
        esc = getattr(service.settings, "escalation", None)
        if esc and getattr(esc, "webhook_url", ""):
            _now = datetime.now(UTC)
            should, ids = service.usage_store.should_escalate(
                classification=triage.classification,
                threshold=esc.block_threshold,
                cooldown_seconds=esc.cooldown_seconds,
                now=_now,
            )
            if should:
                post_escalation(
                    esc.webhook_url,
                    classification=triage.classification,
                    count=len(ids),
                    task_ids=ids,
                    now=_now,
                )
                service.usage_store.record_escalation(
                    classification=triage.classification,
                    task_ids=ids,
                    now=_now,
                )
                # Improve → propose feedback channel: when a pattern becomes
                # systemic (escalation threshold reached), create a single bounded
                # root-cause investigation task rather than leaving the operator to
                # manually diagnose and create it after reading the webhook.
                _systemic_title = (
                    f"[Systemic] Investigate recurring {triage.classification} failures"
                )
                if _systemic_title not in existing_names:
                    _repo_key_esc, _, _ = issue_execution_target(issue, service)
                    _repo_cfg_esc = service.settings.repos.get(_repo_key_esc or "")
                    _base_esc = (_repo_cfg_esc.default_branch if _repo_cfg_esc else "main")
                    try:
                        client.create_issue(
                            name=_systemic_title,
                            description=(
                                f"## Execution\n"
                                f"repo: {_repo_key_esc or 'unknown'}\n"
                                f"base_branch: {_base_esc}\n"
                                f"mode: improve\n\n"
                                f"## Goal\n"
                                f"Investigate and fix the root cause of recurring "
                                f"`{triage.classification}` failures. "
                                f"{len(ids)} tasks have hit this pattern in the last "
                                f"24 hours (threshold: {esc.block_threshold}).\n\n"
                                f"Reason from last triage: {triage.reason_summary}\n\n"
                                f"Affected task IDs: {', '.join(ids[:5])}"
                                f"{'...' if len(ids) > 5 else ''}\n\n"
                                f"## Constraints\n"
                                f"- source: systemic_escalation\n"
                                f"- classification: {triage.classification}\n"
                                f"- Do not create further child tasks — produce a direct fix or a "
                                f"single, bounded follow-up.\n"
                            ),
                            state="Ready for AI",
                            label_names=[
                                "task-kind: improve",
                                f"repo: {_repo_key_esc or 'unknown'}",
                                "source: improve",
                                "urgency: high",
                            ],
                        )
                    except Exception:
                        pass  # Systemic task creation is best-effort
    # S7-4: Self-healing for repeatedly blocked tasks.
    # When the same task has been blocked N consecutive times without a successful
    # execution in between, add a self-healing comment and flag it for human review.
    try:
        _consec = service.usage_store.consecutive_blocks_for_task(task_id, now=datetime.now(UTC))
        if _consec >= CONSECUTIVE_BLOCK_COOLDOWN_THRESHOLD:
            client.comment_issue(
                task_id,
                render_worker_comment(
                    "[Improve] Repeated-block self-healing triggered",
                    [
                        f"task_id: {task_id}",
                        f"consecutive_blocks: {_consec}",
                        f"threshold: {CONSECUTIVE_BLOCK_COOLDOWN_THRESHOLD}",
                        f"last_classification: {triage.classification}",
                        "action: task flagged for human review — autonomous retries paused",
                        "recommendation: review classification pattern and unblock manually or close the task",
                    ],
                ),
            )
            _logger.warning(json.dumps({
                "event": "self_healing_repeated_block",
                "task_id": task_id,
                "consecutive_blocks": _consec,
                "classification": triage.classification,
            }))
    except Exception:
        pass
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
    _slot_id: int = 0,
) -> None:
    """Single-threaded polling loop for a watcher role.

    *_slot_id* is 0 for the primary slot and > 0 for additional parallel
    slots.  Periodic scans (heartbeat write, improve sub-scans, config drift
    check) only run when ``_slot_id == 0`` to prevent duplicate work when
    ``parallel_slots > 1``.
    """
    logger = logging.getLogger(__name__)
    poll_interval_seconds = max(poll_interval_seconds, service.settings.execution_controls().min_watch_interval_seconds)
    cycle = 0
    known_triaged_blocked_ids: set[str] = set()
    counters = {"follow_up_tasks_created": 0, "blocked_tasks_triaged": 0}
    _is_primary_slot = _slot_id == 0
    # Consecutive non-429 errors — used for exponential backoff on transient
    # outages (Plane down, network partition).  Reset to 0 on any successful
    # cycle completion.
    _consecutive_errors = 0

    while True:
        cycle += 1
        cycle_run_id = f"{role}-slot{_slot_id}-cycle-{cycle}"
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
        if status_dir is not None and _is_primary_slot:
            write_heartbeat(status_dir, role, now=datetime.now(UTC))
        # S6-1: Maintenance window gate — skip execution during configured windows.
        _now_mw = datetime.now(UTC)
        if _in_maintenance_window(service.settings, _now_mw):
            logger.info(json.dumps({
                "event": "watch_maintenance_window",
                "role": role,
                "cycle": cycle,
                "hour": _now_mw.hour,
                "weekday": _now_mw.weekday(),
            }))
            if max_cycles is not None and cycle >= max_cycles:
                logger.info(json.dumps({"event": "watch_complete", "role": role, "cycles": cycle, "run_id": cycle_run_id}))
                return
            time.sleep(poll_interval_seconds)
            continue
        try:
            if cycle == 1 and _is_primary_slot:
                if not validate_credentials(
                    service.settings,
                    usage_store=service.usage_store,
                    now=datetime.now(UTC),
                ):
                    logger.error(json.dumps({
                        "event": "watch_credential_failure",
                        "role": role,
                        "action": "restarting",
                    }))
                    sys.exit(1)
                reconciled_ids = reconcile_stale_running_issues(client, role=role, ready_state=ready_state, usage_store=service.usage_store, startup=True)
                if reconciled_ids:
                    logger.info(json.dumps({"event": "watch_reconciled_stale_running", "role": role, "task_ids": reconciled_ids}))
                try:
                    _deleted_ws = cleanup_orphaned_workspaces()
                    if _deleted_ws:
                        logger.info(json.dumps({"event": "watch_cleanup_orphaned_workspaces", "role": role, "deleted": _deleted_ws, "count": len(_deleted_ws)}))
                except Exception:
                    pass
                # Config drift check — log warnings for missing config keys
                _drift_config = os.environ.get("CONTROL_PLANE_CONFIG", "")
                _example_path = Path(_drift_config).parent / "control_plane.example.yaml" if _drift_config else None
                if _drift_config and _example_path and _example_path.exists():
                    try:
                        from control_plane.config.drift import detect_config_drift
                        _drift_gaps = detect_config_drift(_drift_config, _example_path)
                        for _gap in _drift_gaps:
                            logger.warning(json.dumps({
                                "event": "config_drift_detected",
                                "missing_key": _gap,
                                "action": "feature may be silently disabled — add the key to your config",
                            }))
                        if _drift_gaps:
                            logger.warning(json.dumps({
                                "event": "config_drift_summary",
                                "missing_count": len(_drift_gaps),
                                "missing_keys": _drift_gaps,
                            }))
                    except Exception:
                        pass
            # Periodic scans run only on the primary slot to avoid duplicate work.
            if _is_primary_slot:
                # Review revision scan — every 3 cycles for the improve watcher.
                if role == "improve" and cycle % 3 == 0:
                    revision_ids = handle_review_revision_scan(client, service)
                    if revision_ids:
                        counters["follow_up_tasks_created"] += len(revision_ids)
                        logger.info(json.dumps({"event": "watch_review_revisions_created", "role": role, "cycle": cycle, "task_ids": revision_ids}))
                # Merge conflict scan — every 5 cycles for the improve watcher.
                if role == "improve" and cycle % 5 == 0:
                    conflict_ids = handle_merge_conflict_scan(client, service)
                    if conflict_ids:
                        counters["follow_up_tasks_created"] += len(conflict_ids)
                        logger.info(json.dumps({"event": "watch_merge_conflicts_handled", "role": role, "cycle": cycle, "task_ids": conflict_ids}))
                # Stale PR TTL scan — every 20 cycles.
                if role == "improve" and cycle % _STALE_PR_SCAN_CYCLE_INTERVAL == 0:
                    stale_ids = handle_stale_pr_scan(client, service, now=datetime.now(UTC))
                    if stale_ids:
                        counters["follow_up_tasks_created"] += len(stale_ids)
                        logger.info(json.dumps({"event": "watch_stale_prs_closed", "role": role, "cycle": cycle, "task_ids": stale_ids}))
                # Post-merge CI regression scan — every 10 cycles.
                if role == "improve" and cycle % 10 == 0:
                    reg_ids = detect_post_merge_regressions(client, service)
                    if reg_ids:
                        counters["follow_up_tasks_created"] += len(reg_ids)
                        logger.info(json.dumps({"event": "watch_post_merge_regressions", "role": role, "cycle": cycle, "regression_task_ids": reg_ids}))
                # Feedback loop scan — every 15 improve cycles.
                if role == "improve" and cycle % _FEEDBACK_LOOP_CYCLE_INTERVAL == 0:
                    fb_ids = handle_feedback_loop_scan(client, service, now=datetime.now(UTC))
                    if fb_ids:
                        logger.info(json.dumps({"event": "watch_feedback_auto_recorded", "role": role, "cycle": cycle, "task_ids": fb_ids}))
                # Workspace health check — every 25 improve cycles.
                if role == "improve" and cycle % _WORKSPACE_HEALTH_CYCLE_INTERVAL == 0:
                    ws_ids = handle_workspace_health_check(client, service, now=datetime.now(UTC))
                    if ws_ids:
                        counters["follow_up_tasks_created"] += len(ws_ids)
                        logger.info(json.dumps({"event": "watch_workspace_health_tasks", "role": role, "cycle": cycle, "task_ids": ws_ids}))
                # Stale autonomy task scan — every 30 improve cycles.
                if role == "improve" and cycle % _STALE_AUTONOMY_SCAN_CYCLE_INTERVAL == 0:
                    _stale_days = int(getattr(service.settings, "stale_autonomy_backlog_days", _STALE_AUTONOMY_TASK_DAYS))
                    stale_auto_ids = handle_stale_autonomy_task_scan(client, service, now=datetime.now(UTC), stale_days=_stale_days)
                    if stale_auto_ids:
                        logger.info(json.dumps({"event": "watch_stale_autonomy_cancelled", "role": role, "cycle": cycle, "task_ids": stale_auto_ids}))
                # S10-2: Awaiting-input scan — every 8 improve cycles.
                if role == "improve" and cycle % _AWAITING_INPUT_SCAN_CYCLE_INTERVAL == 0:
                    try:
                        ai_ids = handle_awaiting_input_scan(client, service, now=datetime.now(UTC))
                        if ai_ids:
                            counters["follow_up_tasks_created"] += len(ai_ids)
                            logger.info(json.dumps({"event": "watch_awaiting_input_requeued", "role": role, "cycle": cycle, "task_ids": ai_ids}))
                    except Exception:
                        pass
                # S10-10: Priority rescore scan — every 45 improve cycles.
                if role == "improve" and cycle % _PRIORITY_RESCORE_CYCLE_INTERVAL == 0:
                    try:
                        pr_ids = handle_priority_rescore_scan(client, service, now=datetime.now(UTC))
                        if pr_ids:
                            logger.info(json.dumps({"event": "watch_priority_rescored", "role": role, "cycle": cycle, "task_ids": pr_ids}))
                    except Exception:
                        pass
                # S7-5: Dependency update scan — every 50 improve cycles.
                if role == "improve" and cycle % _DEPENDENCY_UPDATE_SCAN_CYCLE_INTERVAL == 0:
                    try:
                        dep_ids = handle_dependency_update_scan(client, service)
                        if dep_ids:
                            counters["follow_up_tasks_created"] += len(dep_ids)
                            logger.info(json.dumps({"event": "watch_dependency_update_tasks", "role": role, "cycle": cycle, "task_ids": dep_ids}))
                    except Exception:
                        pass
                # Stale global editable install cleanup — every 100 improve cycles.
                if role == "improve" and cycle % _STALE_EDITABLE_CLEANUP_CYCLE_INTERVAL == 0:
                    try:
                        _removed = cleanup_stale_global_editables()
                        if _removed:
                            logger.info(json.dumps({"event": "watch_stale_editables_removed", "role": role, "cycle": cycle, "packages": _removed}))
                    except Exception:
                        pass
                # Stale blocked reconcile — cancel superseded follow-ups, re-queue stale
                # validation_failure blocks — every 30 improve cycles.
                if role == "improve" and cycle % _STALE_BLOCKED_RECONCILE_CYCLE_INTERVAL == 0:
                    try:
                        _sb_result = reconcile_stale_blocked_issues(
                            client, ready_state=ready_state, now=datetime.now(UTC)
                        )
                        if _sb_result.get("cancelled"):
                            logger.info(json.dumps({
                                "event": "watch_stale_blocked_cancelled",
                                "role": role,
                                "cycle": cycle,
                                "task_ids": _sb_result["cancelled"],
                            }))
                        if _sb_result.get("requeued"):
                            logger.info(json.dumps({
                                "event": "watch_validation_failure_requeued",
                                "role": role,
                                "cycle": cycle,
                                "task_ids": _sb_result["requeued"],
                            }))
                    except Exception:
                        pass
                    # Orphaned plane/ branch cleanup — same cadence as stale-blocked reconcile.
                    try:
                        _deleted_branches = cleanup_orphaned_plane_branches(client, service)
                        if _deleted_branches:
                            logger.info(json.dumps({
                                "event": "watch_orphaned_branches_deleted",
                                "role": role,
                                "cycle": cycle,
                                "branches": _deleted_branches,
                            }))
                    except Exception:
                        pass
                    # Campaign tracker completion — close [Campaign] parents when all children resolve.
                    try:
                        _closed_campaigns = reconcile_campaign_trackers(client)
                        if _closed_campaigns:
                            logger.info(json.dumps({
                                "event": "watch_campaign_trackers_closed",
                                "role": role,
                                "cycle": cycle,
                                "task_ids": _closed_campaigns,
                            }))
                    except Exception:
                        pass
                # S6-4: Failure-rate degradation check — every 5 cycles on primary slot.
                if cycle % 5 == 0:
                    try:
                        _degraded_rate = service.usage_store.check_failure_rate_degradation(now=datetime.now(UTC))
                        if _degraded_rate is not None:
                            logger.warning(json.dumps({
                                "event": "failure_rate_degradation",
                                "role": role,
                                "cycle": cycle,
                                "success_rate": round(_degraded_rate, 3),
                                "action": "review recent executions before circuit breaker opens",
                            }))
                    except Exception:
                        pass
                    # S7-7: Escalate when the circuit breaker has tripped.
                    try:
                        _cb_budget = service.usage_store.budget_decision(now=datetime.now(UTC))
                        if not _cb_budget.allowed and "circuit_breaker" in (_cb_budget.reason or ""):
                            _esc7 = getattr(service.settings, "escalation", None)
                            if _esc7 and getattr(_esc7, "webhook_url", ""):
                                _now7 = datetime.now(UTC)
                                _cb_should, _cb_ids = service.usage_store.should_escalate(
                                    classification="circuit_breaker_tripped",
                                    threshold=1,
                                    cooldown_seconds=int(getattr(_esc7, "cooldown_seconds", 3600)),
                                    now=_now7,
                                )
                                if _cb_should:
                                    post_escalation(
                                        _esc7.webhook_url,
                                        classification="circuit_breaker_tripped",
                                        count=1,
                                        task_ids=[],
                                        now=_now7,
                                    )
                                    service.usage_store.record_escalation(
                                        classification="circuit_breaker_tripped",
                                        task_ids=[],
                                        now=_now7,
                                    )
                                    logger.error(json.dumps({
                                        "event": "circuit_breaker_escalation_sent",
                                        "role": role,
                                        "cycle": cycle,
                                        "reason": _cb_budget.reason,
                                    }))
                    except Exception:
                        pass
                # S6-10: Board health snapshot — every 40 improve cycles.
                if role == "improve" and cycle % 40 == 0:
                    try:
                        _bh_issues = client.list_issues()
                        _bh_anomalies = board_health_check(_bh_issues, service)
                        if _bh_anomalies:
                            logger.warning(json.dumps({
                                "event": "board_health_anomalies",
                                "role": role,
                                "cycle": cycle,
                                "anomalies": _bh_anomalies,
                            }))
                    except Exception:
                        pass
                # Periodic orphan recovery: reconcile stale Running tasks in case a
                # worker was killed mid-execution after the startup reconciliation ran.
                # Runs every 20 cycles so a task whose TTL expires between restarts
                # is recovered within one poll-interval window after the TTL passes.
                if role in {"goal", "test", "improve"} and cycle % 20 == 0:
                    try:
                        _recon_ids = reconcile_stale_running_issues(
                            client, role=role, ready_state=ready_state,
                            usage_store=service.usage_store,
                        )
                        if _recon_ids:
                            logger.info(json.dumps({
                                "event": "watch_reconciled_stale_running",
                                "role": role,
                                "cycle": cycle,
                                "task_ids": _recon_ids,
                            }))
                    except Exception:
                        pass
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
            elif action in ("blocked_triage", "blocked_resolution_complete", "blocked_pr_merged", "blocked_stale_escalation"):
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
                # Pass the kodo resource gate into execution_gate_decision so it
                # runs AFTER budget/noop checks but BEFORE record_execution.
                # This prevents a concurrency-cap skip from writing a signature
                # that would cause the next cycle to treat the task as a no-op.
                _kodo_gate_fn = (
                    (lambda: _check_kodo_execution_gate(service.settings))
                    if action == "execute" and role in {"goal", "test"}
                    else None
                )
                gate = execution_gate_decision(
                    service=service,
                    role=role,
                    action=action,
                    issue=issue,
                    kodo_gate_check=_kodo_gate_fn,
                )
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
                    if gate_action == "skip_noop":
                        # kodo already ran on this exact task state and made no changes.
                        # The goal is already satisfied — close it as Done.
                        client.transition_issue(task_id, "Done")
                        client.comment_issue(
                            task_id,
                            render_worker_comment(
                                f"[{worker_title(role)}] No-op: task auto-closed",
                                [
                                    f"task_id: {task_id}",
                                    f"task_kind: {task_kind}",
                                    "result_status: done",
                                    f"reason: {evidence.get('reason', 'no_op')}",
                                    f"detail: {evidence.get('detail', 'no_state_change')}",
                                ],
                            ),
                        )
                    elif gate_action == "retry_cap_block":
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
                    # kodo_gate_blocked: resource contention, no board state change needed.
                    # Task stays in Ready for AI and is retried next cycle.
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
                # Pre-execution validation for goal tasks
                if role == "goal" and action == "execute":
                    if not validate_task_pre_execution(client, service, task_id, issue):
                        # S6-6: Feed rejection back into the proposal learning loop.
                        try:
                            _pre_exec_category = str(
                                next(
                                    (lbl for lbl in issue_label_names(issue) if lbl.startswith("task-kind")),
                                    "goal",
                                )
                            )
                            service.usage_store.record_proposal_outcome(
                                category=_pre_exec_category,
                                succeeded=False,
                                now=datetime.now(UTC),
                            )
                        except Exception:
                            pass
                        write_watch_status(
                            status_dir=status_dir,
                            role=role,
                            cycle=cycle,
                            state="idle",
                            run_id=cycle_run_id,
                            last_action="pre_exec_rejected",
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
                            _esc_repo_key = _extract_repo_key(issue_for_escalation, service)
                            _esc_labels = ["task-kind: goal", f"repo: {_esc_repo_key}", "source: improve-worker"]
                            if _is_self_repo(_esc_repo_key, service) or _self_modify_approved(issue_for_escalation):
                                _esc_labels.append("self-modify: approved")
                            escalation_task = client.create_issue(
                                name=f"Re-triage: {issue_title}",
                                description=(
                                    f"## Execution\nrepo: {_esc_repo_key}\nmode: goal\n\n"
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
                                label_names=_esc_labels,
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
                # Backlog gate: skip proposal generation when the Ready for AI
                # queue already has enough tasks waiting.
                _skip_threshold = getattr(service.settings, "propose_skip_when_ready_count", 8)
                _ready_count = 0
                if _skip_threshold > 0:
                    try:
                        _all_issues = client.list_issues()
                        _ready_count = sum(1 for _i in _all_issues if issue_status_name(_i) == ready_state)
                    except Exception:
                        pass
                if _skip_threshold > 0 and _ready_count >= _skip_threshold:
                    logger.info(json.dumps({
                        "event": "watch_propose_skipped_backlog",
                        "role": role,
                        "cycle": cycle,
                        "ready_count": _ready_count,
                        "threshold": _skip_threshold,
                        "run_id": cycle_run_id,
                    }))
                    write_watch_status(
                        status_dir=status_dir,
                        role=role,
                        cycle=cycle,
                        state="idle",
                        run_id=cycle_run_id,
                        last_action="propose_skipped_backlog",
                        counters=counters,
                    )
                    if max_cycles is not None and cycle >= max_cycles:
                        logger.info(json.dumps({"event": "watch_complete", "role": role, "cycles": cycle, "run_id": cycle_run_id}))
                        return
                    logger.info(json.dumps({"event": "watch_cycle_end", "role": role, "cycle": cycle, "sleep_interval_seconds": poll_interval_seconds, "run_id": cycle_run_id}))
                    time.sleep(poll_interval_seconds)
                    continue
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
            _consecutive_errors += 1
            logger.info(json.dumps({"event": "watch_error", "role": role, "cycle": cycle, "message": str(exc).replace('"', "'"), "run_id": cycle_run_id}))
            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="error", run_id=cycle_run_id, last_action="watch_error", counters=counters)
        except Exception as exc:
            # Transient errors (connection refused, DNS failure, timeout) use
            # exponential backoff — cap at 5 minutes so recovery is timely once
            # the API is reachable again.
            _consecutive_errors += 1
            _error_backoff = min(
                poll_interval_seconds * (2 ** min(_consecutive_errors - 1, 4)),
                300,
            )
            logger.info(json.dumps({
                "event": "watch_error",
                "role": role,
                "cycle": cycle,
                "consecutive_errors": _consecutive_errors,
                "backoff_seconds": _error_backoff,
                "message": str(exc).replace('"', "'"),
                "run_id": cycle_run_id,
            }))
            write_watch_status(status_dir=status_dir, role=role, cycle=cycle, state="error", run_id=cycle_run_id, last_action="watch_error", counters=counters)
            if max_cycles is not None and cycle >= max_cycles:
                logger.info(json.dumps({"event": "watch_complete", "role": role, "cycles": cycle, "run_id": cycle_run_id}))
                return
            time.sleep(_error_backoff)
            continue
        else:
            # Successful cycle: reset consecutive-error counter
            _consecutive_errors = 0
            if _is_primary_slot:
                try:
                    _snap_issues = client.list_issues()
                    write_board_snapshot(_snap_issues, role=role, status_dir=status_dir)
                except Exception:
                    pass

        if max_cycles is not None and cycle >= max_cycles:
            logger.info(json.dumps({"event": "watch_complete", "role": role, "cycles": cycle, "run_id": cycle_run_id}))
            return
        logger.info(json.dumps({"event": "watch_cycle_end", "role": role, "cycle": cycle, "sleep_interval_seconds": poll_interval_seconds, "run_id": cycle_run_id}))
        time.sleep(poll_interval_seconds)


def run_parallel_watch_loop(
    client: PlaneClient,
    service: ExecutionService,
    *,
    role: str,
    ready_state: str,
    poll_interval_seconds: int,
    max_cycles: int | None,
    status_dir: Path | None = None,
    n_slots: int = 1,
) -> None:
    """Launch *n_slots* parallel task-execution threads for a watcher role.

    When *n_slots* == 1 this is identical to calling ``run_watch_loop``
    directly.  When *n_slots* > 1, slot 0 is the primary slot (runs periodic
    scans, heartbeat, credential validation); remaining slots only execute
    tasks.  All threads share the same ``client`` and ``service`` objects —
    the Plane API's state machine is the distributed lock that prevents two
    slots from claiming the same task.
    """
    import threading

    if n_slots <= 1:
        run_watch_loop(
            client,
            service,
            role=role,
            ready_state=ready_state,
            poll_interval_seconds=poll_interval_seconds,
            max_cycles=max_cycles,
            status_dir=status_dir,
            _slot_id=0,
        )
        return

    threads: list[threading.Thread] = []
    for slot_id in range(n_slots):
        t = threading.Thread(
            target=run_watch_loop,
            kwargs={
                "client": client,
                "service": service,
                "role": role,
                "ready_state": ready_state,
                "poll_interval_seconds": poll_interval_seconds,
                "max_cycles": max_cycles,
                "status_dir": status_dir,
                "_slot_id": slot_id,
            },
            name=f"watch-{role}-slot{slot_id}",
            daemon=True,
        )
        threads.append(t)

    logger = logging.getLogger(__name__)
    logger.info(json.dumps({
        "event": "parallel_watch_start",
        "role": role,
        "n_slots": n_slots,
    }))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    logger.info(json.dumps({
        "event": "parallel_watch_complete",
        "role": role,
        "n_slots": n_slots,
    }))


def main() -> None:
    # Quick subcommands that don't need a Plane connection
    if len(sys.argv) >= 2 and sys.argv[1] == "heartbeat-check":
        hb_parser = argparse.ArgumentParser(description="Check watcher heartbeats")
        hb_parser.add_argument("--log-dir", default="logs/local")
        hb_args = hb_parser.parse_args(sys.argv[2:])
        log_dir = Path(hb_args.log_dir)
        stale = check_heartbeats(log_dir)
        if stale:
            print(f"STALE watchers: {', '.join(stale)}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] == "spend-report":
        sr_parser = argparse.ArgumentParser(description="Show execution spend report")
        sr_parser.add_argument("--window-days", type=int, default=1)
        sr_parser.add_argument("--usage-path", default=None)
        sr_args = sr_parser.parse_args(sys.argv[2:])
        from control_plane.execution.usage_store import UsageStore
        store = UsageStore(Path(sr_args.usage_path) if sr_args.usage_path else None)
        report = store.get_spend_report(window_days=sr_args.window_days, now=datetime.now(UTC))
        print(json.dumps(report, indent=2))
        sys.exit(0)

    # S6-9: Structured audit log export
    if len(sys.argv) >= 2 and sys.argv[1] == "audit-export":
        ae_parser = argparse.ArgumentParser(description="Export structured audit log")
        ae_parser.add_argument("--window-days", type=int, default=7)
        ae_parser.add_argument("--usage-path", default=None)
        ae_args = ae_parser.parse_args(sys.argv[2:])
        from control_plane.execution.usage_store import UsageStore
        store = UsageStore(Path(ae_args.usage_path) if ae_args.usage_path else None)
        events = store.audit_export(window_days=ae_args.window_days, now=datetime.now(UTC))
        print(json.dumps(events, indent=2))
        sys.exit(0)

    # S6-10: Board health snapshot
    if len(sys.argv) >= 2 and sys.argv[1] == "board-health":
        bh_parser = argparse.ArgumentParser(description="Check board health")
        bh_parser.add_argument("--config", required=True)
        bh_args = bh_parser.parse_args(sys.argv[2:])
        _bh_settings = load_settings(bh_args.config)
        _bh_client = PlaneClient(
            base_url=_bh_settings.plane.base_url,
            api_token=_bh_settings.plane_token(),
            workspace_slug=_bh_settings.plane.workspace_slug,
            project_id=_bh_settings.plane.project_id,
        )
        try:
            _bh_service = ExecutionService(_bh_settings)
            _bh_issues_all = _bh_client.list_issues()
            _bh_result = board_health_check(_bh_issues_all, _bh_service)
            print(json.dumps({"anomalies": _bh_result, "total_issues": len(_bh_issues_all)}, indent=2))
        finally:
            _bh_client.close()
        sys.exit(0)

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
    parser.add_argument(
        "--parallel-slots",
        type=int,
        default=None,
        help="Number of parallel task-execution threads (overrides config parallel_slots; default 1)",
    )
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
            _n_slots = args.parallel_slots if args.parallel_slots is not None else int(getattr(settings, "parallel_slots", 1))
            run_parallel_watch_loop(
                client,
                service,
                role=args.role,
                ready_state=args.ready_state,
                poll_interval_seconds=args.poll_interval_seconds,
                max_cycles=args.max_cycles,
                status_dir=Path(args.status_dir) if args.status_dir else None,
                n_slots=_n_slots,
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
