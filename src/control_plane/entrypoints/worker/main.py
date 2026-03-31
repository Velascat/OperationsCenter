from __future__ import annotations

import argparse
import html
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService
from control_plane.config import load_settings
from control_plane.domain import ExecutionResult

TRIAGE_COMMENT_MARKER = "[Improve] Blocked triage"
IMPROVE_COMMENT_MARKER = "[Improve] Improvement pass"
RATE_LIMIT_BACKOFF_MULTIPLIER = 4
UNKNOWN_BLOCKED_CLASSIFICATION = "unknown"


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


def status_file_path(status_dir: Path | None, role: str) -> Path | None:
    if status_dir is None:
        return None
    status_dir.mkdir(parents=True, exist_ok=True)
    return status_dir / f"{role}.status.json"


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


def latest_run_dir(result: ExecutionResult) -> Path | None:
    for artifact in result.artifacts:
        artifact_path = Path(artifact)
        if artifact_path.name == "result_summary.md":
            return artifact_path.parent
    return None


def run_service_task(service: ExecutionService, client: PlaneClient, task_id: str, *, worker_role: str) -> ExecutionResult:
    try:
        return service.run_task(client, task_id, worker_role=worker_role)
    except TypeError as exc:
        if "worker_role" not in str(exc):
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


def blocked_issue_already_triaged(client: PlaneClient, task_id: str) -> bool:
    for comment in client.list_comments(task_id):
        if TRIAGE_COMMENT_MARKER.lower() in extract_comment_text(comment).lower():
            return True
    return False


def classify_blocked_issue(issue: dict[str, Any], comments: list[dict[str, Any]]) -> tuple[str, str]:
    chunks = [str(issue.get("name", "")), str(issue.get("description", "")), str(issue.get("description_html", ""))]
    chunks.extend(extract_comment_text(comment) for comment in comments)
    lowered = "\n".join(chunk for chunk in chunks if chunk).lower()

    if "policy_violations:" in lowered or "policy=failed" in lowered:
        return "scope_policy", "Changes landed outside the allowed repo scope."
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


def default_repo_key(service: ExecutionService) -> str:
    return next(iter(service.settings.repos.keys()))


def existing_issue_names(client: PlaneClient) -> set[str]:
    names: set[str] = set()
    for issue in client.list_issues():
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
    repo_key = default_repo_key(service)
    repo_cfg = service.settings.repos[repo_key]
    lines = [
        "## Execution",
        f"repo: {repo_key}",
        f"base_branch: {repo_cfg.default_branch}",
        "mode: goal",
    ]
    allowed_paths = ["src/", "tests/"] if repo_key.lower() == "controlplane" or repo_key.lower() == "control-plane" else []
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


def discover_improvement_candidates(service: ExecutionService) -> tuple[list[dict[str, str]], list[str]]:
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

    repo_key = default_repo_key(service)
    repo_cfg = service.settings.repos[repo_key]
    workspace_path = service.workspace.create()
    try:
        repo_path = service.git.clone(repo_cfg.clone_url, workspace_path)
        service.git.checkout_base(repo_path, repo_cfg.default_branch)
        top_level = sorted(
            path.name
            for path in repo_path.iterdir()
            if path.name != ".git"
        )
        report_notes.append(f"- inspected repo: {repo_key} @ {repo_cfg.default_branch}")
        report_notes.append(f"- top-level entries: {', '.join(top_level[:10]) or '(none)'}")
        report_notes.append(f"- tests directory present: {'yes' if (repo_path / 'tests').exists() else 'no'}")
        report_notes.append(f"- docs directory present: {'yes' if (repo_path / 'docs').exists() else 'no'}")
    finally:
        service.workspace.cleanup(workspace_path)

    unique: dict[str, dict[str, str]] = {}
    for finding in findings:
        unique.setdefault(finding["title"].strip().lower(), finding)
    return list(unique.values())[:3], report_notes


def handle_goal_task(client: PlaneClient, service: ExecutionService, task_id: str) -> list[str]:
    issue = client.fetch_issue(task_id)
    result = run_service_task(service, client, task_id, worker_role="goal")
    created_ids: list[str] = []
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
            follow_up = create_follow_up_task_if_missing(
                client,
                service,
                existing_names=existing_names,
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
    result = run_service_task(service, client, task_id, worker_role="test")
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
    findings, report_notes = discover_improvement_candidates(service)
    created_ids: list[str] = []
    created_titles: list[str] = []

    for finding in findings:
        created = create_follow_up_task_if_missing(
            client,
            service,
            existing_names=existing_names,
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
    classification, rationale = classify_blocked_issue(issue, comments)
    created_ids: list[str] = []

    target_task_kind = "goal"
    if task_kind_for_issue(issue) == "improve" and classification == "validation_failure":
        target_task_kind = "test"

    if classification not in {UNKNOWN_BLOCKED_CLASSIFICATION, "infra_tooling"} and not issue_is_unblock_chain(issue):
        follow_up = create_follow_up_task(
            client,
            service,
            source_role="improve",
            task_kind=target_task_kind,
            original_issue=issue,
            title=f"Resolve blocked {issue.get('name', 'task')}",
            goal_text=(
                f"Resolve the blocked task '{issue.get('name', 'Untitled')}' by addressing the classified failure: {classification}."
            ),
            handoff_reason=f"improve_triage_{classification}",
            constraints_text=(
                f"- source_task_id: {task_id}\n"
                f"- classification: {classification}\n"
                f"- rationale: {rationale}"
            ),
        )
        created_ids.append(str(follow_up.get("id")))
    elif issue_is_unblock_chain(issue):
        rationale = (
            f"{rationale} Improve already generated this unblock task, so the watcher will not create another "
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
                f"blocked_classification: {classification}",
                f"reason: {rationale}",
                f"follow_up_task_ids: {', '.join(created_ids) if created_ids else 'none'}",
                f"next_action: {'follow-up task created' if created_ids else 'human attention or existing context required'}",
                f"handoff_reason: {'improve_triage_created_follow_up' if created_ids else 'improve_triage_human_attention'}",
            ],
        ),
    )
    client.transition_issue(task_id, "Blocked")
    return classification, created_ids


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
                client.transition_issue(task_id, "Running")
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
                else:
                    raise ValueError(f"Unsupported worker role '{role}'")
        except ValueError as exc:
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
    parser.add_argument("--role", default="goal", choices=["goal", "test", "improve"])
    parser.add_argument("--poll-interval-seconds", type=int, default=15)
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
