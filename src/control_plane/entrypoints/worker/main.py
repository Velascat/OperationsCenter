from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import html
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService, TaskParser
from control_plane.config import load_settings
from control_plane.domain import ExecutionResult
TRIAGE_COMMENT_MARKER = "[Improve] Blocked triage"
IMPROVE_COMMENT_MARKER = "[Improve] Improvement pass"
PROPOSE_COMMENT_MARKER = "[Propose] Autonomous task created"
RATE_LIMIT_BACKOFF_MULTIPLIER = 4
UNKNOWN_BLOCKED_CLASSIFICATION = "unknown"
MAX_IMPROVE_FOLLOW_UPS_PER_CYCLE = 3
MAX_PROPOSALS_PER_CYCLE = 2
MAX_PROPOSALS_PER_DAY = 6
PROPOSAL_COOLDOWN_SECONDS = 30 * 60
PROPOSAL_WINDOW_SECONDS = 24 * 60 * 60
LOW_BACKLOG_THRESHOLD = 2
EXECUTION_ACTIONS = {"execute", "improve_task"}
MAX_CLASSIFICATION_ISSUES = 20


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
    if not result.validation_passed:
        return "validation_failure"
    excerpt = (result.execution_stderr_excerpt or "").lower()
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
        return {"last_proposal_at": None, "proposal_timestamps": []}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"last_proposal_at": None, "proposal_timestamps": []}
    return {
        "last_proposal_at": payload.get("last_proposal_at"),
        "proposal_timestamps": list(payload.get("proposal_timestamps", [])),
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


def rewrite_worker_summary(result: ExecutionResult, service: ExecutionService) -> None:
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


def board_is_idle_for_proposals_from_issues(issues: list[dict[str, Any]]) -> bool:
    open_count = 0
    ready_or_running_count = 0
    for issue in issues:
        if not is_open_issue(issue):
            continue
        if issue_task_kind(issue) not in {"goal", "test"}:
            continue
        open_count += 1
        if issue_status_name(issue) in {"Ready for AI", "Running"}:
            ready_or_running_count += 1
    return ready_or_running_count == 0 and open_count <= LOW_BACKLOG_THRESHOLD


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
        if status_name == ready_state:
            return task_id
        if not status_name or status_name == str(issue.get("state", "")):
            detailed_issue = client.fetch_issue(task_id)
            if issue_task_kind(detailed_issue) == role and issue_status_name(detailed_issue) == ready_state:
                return task_id
    raise ValueError(f"No work item found in state '{ready_state}'")


def select_watch_candidate(
    client: PlaneClient,
    *,
    ready_state: str,
    role: str,
    known_triaged_blocked_ids: set[str] | None = None,
) -> tuple[str, str]:
    issues = client.list_issues()
    if role == "improve":
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
                return task_id, "blocked_triage"
            if known_triaged_blocked_ids is not None:
                known_triaged_blocked_ids.add(task_id)
        raise ValueError("No improve work item found for blocked triage or improve task routing")

    task_id = select_ready_task_id(client, ready_state=ready_state, role=role)
    return task_id, "execute"


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


def blocked_issue_already_triaged(client: PlaneClient, task_id: str) -> bool:
    for comment in client.list_comments(task_id):
        if TRIAGE_COMMENT_MARKER.lower() in extract_comment_text(comment).lower():
            return True
    return False


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


def reconcile_stale_running_issues(client: PlaneClient, *, role: str, ready_state: str) -> list[str]:
    if role not in {"goal", "test", "improve"}:
        return []
    reconciled: list[str] = []
    for issue in client.list_issues():
        if issue_status_name(issue) != "Running":
            continue
        task_kind = issue_task_kind(issue)
        if role == "improve":
            if task_kind != "improve":
                continue
        elif task_kind != role:
            continue
        task_id = str(issue["id"])
        client.transition_issue(task_id, ready_state)
        client.comment_issue(
            task_id,
            render_worker_comment(
                f"[{worker_title(role)}] Reconciled stale running state",
                [
                    f"task_id: {task_id}",
                    f"task_kind: {task_kind}",
                    "result_status: ready_for_ai",
                    "reason: task was left in Running without an active worker completion path",
                ],
            ),
        )
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
            "parse",
            "config",
        ]
    ):
        return "parse_config", "The work item contract or repo configuration is invalid for execution."
    return UNKNOWN_BLOCKED_CLASSIFICATION, "The failure needs human review or a more specific follow-up task."


def build_improve_triage_result(
    client: PlaneClient,
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
) -> ImproveTriageResult:
    classification, rationale = classify_blocked_issue(issue, comments)
    classification_counts = recent_classification_counts(client)
    # Treat one existing classified failure plus the current blocked task as a
    # meaningful recurring pattern worth collapsing into a single system-fix task.
    repeated_pattern = classification_counts.get(classification, 0) >= 1
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

    follow_up = ImproveFollowUpSpec(
        task_kind=task_kind,
        title=f"Resolve blocked {issue_title}",
        goal_text=goal_text,
        handoff_reason=f"improve_triage_{classification}",
        constraints_text=(
            f"- source_task_id: {issue.get('id')}\n"
            f"- source_task_kind: {issue_kind}\n"
            f"- classification: {classification}\n"
            f"- rationale: {rationale}"
        ),
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
        except ValueError:
            pass

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
    workspace_path = service.workspace.create()
    try:
        repo_path = service.git.clone(repo_cfg.clone_url, workspace_path)
        service.git.checkout_base(repo_path, target_branch)
        top_level = sorted(
            path.name
            for path in repo_path.iterdir()
            if path.name != ".git"
        )
        report_notes.append(f"- inspected repo: {repo_key} @ {target_branch}")
        recent_commits = service.git.recent_commits(repo_path, max_count=5)
        if recent_commits:
            report_notes.append(f"- recent commits: {' | '.join(recent_commits[:3])}")
        recent_files = service.git.recent_changed_files(repo_path, max_count=3)
        if recent_files:
            report_notes.append(f"- recently changed files: {', '.join(recent_files[:5])}")
        report_notes.append(f"- top-level entries: {', '.join(top_level[:10]) or '(none)'}")
        report_notes.append(f"- tests directory present: {'yes' if (repo_path / 'tests').exists() else 'no'}")
        report_notes.append(f"- docs directory present: {'yes' if (repo_path / 'docs').exists() else 'no'}")
    finally:
        service.workspace.cleanup(workspace_path)

    unique: dict[str, dict[str, str]] = {}
    for finding in findings:
        unique.setdefault(finding["title"].strip().lower(), finding)
    return list(unique.values())[:3], report_notes


def evidence_lines_from_notes(notes: list[str]) -> list[str]:
    values = [note.removeprefix("- ").strip() for note in notes if note.strip()]
    priorities = (
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
    board_idle = board_is_idle_for_proposals_from_issues(issues)
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

    if board_idle:
        findings, report_notes = discover_improvement_candidates(
            service,
            repo_key=repo_key,
            base_branch=service.settings.repos[repo_key].default_branch,
        )
        notes.extend(report_notes)
        proposals.extend(proposal_specs_from_findings(findings, repo_key=repo_key, signal_prefix="idle_board"))

    if board_idle and not proposals:
        evidence_lines = evidence_lines_from_notes(report_notes[:6])
        if idle_board_evidence_is_strong(findings=findings, evidence_lines=evidence_lines):
            proposals.append(idle_board_fallback_proposal(repo_key=repo_key, evidence_lines=evidence_lines))
            notes.append("fallback_proposal: idle_board evidence-anchored repo improvement")
        else:
            notes.append("fallback_suppressed: idle_board evidence too weak")

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
) -> dict[str, Any] | None:
    normalized_title = proposal.title.strip().lower()
    normalized_key = proposal.dedup_key.strip().lower()
    if normalized_title in existing_names or normalized_key in proposal_keys:
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

    for proposal in proposals:
        if len(created_ids) >= MAX_PROPOSALS_PER_CYCLE:
            break
        created = create_proposed_task_if_missing(
            client,
            service,
            proposal=proposal,
            existing_names=existing_names,
            proposal_keys=proposal_keys,
        )
        if created is None:
            continue
        created_ids.append(str(created.get("id")))
        created_states.add(proposal.recommended_state)

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
        rewrite_worker_summary(result, service)
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
        rewrite_worker_summary(result, service)
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
    rewrite_worker_summary(result, service)
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
        rewrite_worker_summary(result, service)
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
        rewrite_worker_summary(result, service)
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
    rewrite_worker_summary(result, service)
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
            task_id, action = select_watch_candidate(
                client,
                ready_state=ready_state,
                role=role,
                known_triaged_blocked_ids=known_triaged_blocked_ids,
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
            elif action == "blocked_triage":
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
                        client.transition_issue(task_id, ready_state if action != "blocked_triage" else "Blocked")
                        client.comment_issue(
                            task_id,
                            render_worker_comment(
                                f"[{worker_title(role)}] Task returned to queue after worker error",
                                [
                                    f"task_id: {task_id}",
                                    f"task_kind: {task_kind}",
                                    f"action: {action}",
                                    f"result_status: {(ready_state if action != 'blocked_triage' else 'blocked').lower().replace(' ', '_')}",
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
