from __future__ import annotations

import argparse
import html
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService
from control_plane.config import load_settings

TRIAGE_COMMENT_MARKER = "Blocked triage result"
IMPROVE_COMMENT_MARKER = "Improve worker result"
RATE_LIMIT_BACKOFF_MULTIPLIER = 4


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
        return "scope-policy violation", "Changes landed outside the allowed repo scope."
    if "validation_passed: false" in lowered or "validation=failed" in lowered:
        return "validation failure", "Validation failed after execution."
    if any(
        token in lowered
        for token in [
            "no such file or directory",
            "phase: bootstrap",
            "phase: kodo",
            "phase: repo_setup",
            "tooling",
            "installation failed",
        ]
    ):
        return "infra/tooling", "The run failed because tooling or the local execution environment was unavailable."
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
        return "parse/config issue", "The work item contract or repo configuration is invalid for execution."
    return "unknown/manual attention", "The failure needs human review or a more specific follow-up task."


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
    original_issue: dict[str, Any],
    goal_text: str,
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
    constraints_text: str | None = None,
    state: str = "Ready for AI",
) -> dict[str, Any]:
    description = build_follow_up_description(
        service=service,
        original_issue=original_issue,
        goal_text=goal_text,
        constraints_text=constraints_text,
    )
    return client.create_issue(
        name=title,
        description=description,
        state=state,
        label_names=[f"task-kind: {task_kind}", f"source: {source_role}-worker"],
    )


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
    result = service.run_task(client, task_id)
    created_ids: list[str] = []
    if not result.success and result.validation_passed and not result.changed_files:
        existing_names = existing_issue_names(client)
        follow_up = create_follow_up_task_if_missing(
            client,
            service,
            existing_names=existing_names,
            source_role="goal",
            task_kind="improve",
            original_issue=issue,
            title=f"Investigate no-op execution for {issue.get('name', 'goal task')}",
            goal_text=(
                f"Inspect why the goal task '{issue.get('name', 'Untitled')}' failed without producing any repository changes "
                "and turn the finding into one or more bounded follow-up tasks."
            ),
            constraints_text=(
                f"- source_task_id: {task_id}\n"
                f"- source_run_id: {result.run_id}\n"
                "- focus on execution-path diagnosis before proposing more implementation work"
            ),
        )
        if follow_up is not None:
            created_ids.append(str(follow_up.get("id")))
            client.comment_issue(
                task_id,
                "\n".join(
                    [
                        "Goal worker created an improve follow-up task",
                        f"- follow_up_task_id: {follow_up.get('id')}",
                        f"- follow_up_title: {follow_up.get('name')}",
                        "- reason: execution failed without code changes",
                    ]
                ),
            )
    return created_ids


def handle_test_task(client: PlaneClient, service: ExecutionService, task_id: str) -> list[str]:
    issue = client.fetch_issue(task_id)
    result = service.run_task(client, task_id)
    if result.success:
        client.transition_issue(task_id, "Done")
        client.comment_issue(
            task_id,
            "\n".join(
                [
                    "Test worker completed verification",
                    f"- run_id: {result.run_id}",
                    "- status: done",
                ]
            ),
        )
        return []

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
        constraints_text=(
            f"- source_task_id: {task_id}\n"
            f"- source_run_id: {result.run_id}\n"
            "- keep the fix scoped to the failure described in the test task"
        ),
    )
    client.comment_issue(
        task_id,
        "\n".join(
            [
                "Test worker created a follow-up goal task",
                f"- follow_up_task_id: {follow_up.get('id')}",
                f"- follow_up_title: {follow_up.get('name')}",
                "- status: blocked",
            ]
        ),
    )
    return [str(follow_up.get("id"))]


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
            constraints_text=(
                f"- source_task_id: {task_id}\n"
                f"{finding['constraints']}"
            ),
        )
        if created is not None:
            created_ids.append(str(created.get("id")))
            created_titles.append(str(created.get("name")))

    comment_lines = [
        IMPROVE_COMMENT_MARKER,
        *report_notes,
        f"- created_task_ids: {', '.join(created_ids) if created_ids else 'none'}",
    ]
    if findings:
        comment_lines.append("- discovered_findings:")
        for finding in findings:
            comment_lines.append(f"- {finding['title']}")
            comment_lines.append(finding["note"])

    client.comment_issue(
        task_id,
        "\n".join(comment_lines),
    )
    client.transition_issue(task_id, "Review" if created_ids else "Done")
    return created_ids


def handle_blocked_triage(client: PlaneClient, service: ExecutionService, task_id: str) -> tuple[str, list[str]]:
    issue = client.fetch_issue(task_id)
    comments = client.list_comments(task_id)
    classification, rationale = classify_blocked_issue(issue, comments)
    created_ids: list[str] = []

    if classification != "unknown/manual attention" and not issue_is_unblock_chain(issue):
        follow_up = create_follow_up_task(
            client,
            service,
            source_role="improve",
            task_kind="goal",
            original_issue=issue,
            title=f"Unblock {issue.get('name', 'blocked task')}",
            goal_text=(
                f"Unblock the task '{issue.get('name', 'Untitled')}' by resolving the classified failure: {classification}."
            ),
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
        "\n".join(
            [
                TRIAGE_COMMENT_MARKER,
                f"- classification: {classification}",
                f"- rationale: {rationale}",
                f"- follow_up_task_ids: {', '.join(created_ids) if created_ids else 'none'}",
                "- status: blocked",
            ]
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
) -> None:
    logger = logging.getLogger(__name__)
    cycle = 0
    known_triaged_blocked_ids: set[str] = set()

    while True:
        cycle += 1
        logger.info(
            '{"event":"watch_cycle_start","role":"%s","cycle":%d,"poll_interval_seconds":%d}',
            role,
            cycle,
            poll_interval_seconds,
        )
        try:
            task_id, action = select_watch_candidate(
                client,
                ready_state=ready_state,
                role=role,
                known_triaged_blocked_ids=known_triaged_blocked_ids,
            )
            logger.info(
                '{"event":"watch_task_selected","role":"%s","cycle":%d,"task_id":"%s","action":"%s"}',
                role,
                cycle,
                task_id,
                action,
            )
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
                logger.info(
                    '{"event":"watch_task_skipped","role":"%s","cycle":%d,"task_id":"%s","reason":"state_or_kind_changed","task_kind":"%s","status":"%s"}',
                    role,
                    cycle,
                    task_id,
                    task_kind,
                    status_name,
                )
            else:
                client.transition_issue(task_id, "Running")
                logger.info(
                    '{"event":"watch_task_claimed","role":"%s","cycle":%d,"task_id":"%s","task_kind":"%s","action":"%s"}',
                    role,
                    cycle,
                    task_id,
                    task_kind,
                    action,
                )
                if role == "goal":
                    created_ids = handle_goal_task(client, service, task_id)
                    logger.info(
                        '{"event":"watch_action_complete","role":"%s","cycle":%d,"task_id":"%s","action":"execute","created_task_ids":"%s"}',
                        role,
                        cycle,
                        task_id,
                        ",".join(created_ids),
                    )
                elif role == "test":
                    created_ids = handle_test_task(client, service, task_id)
                    logger.info(
                        '{"event":"watch_action_complete","role":"%s","cycle":%d,"task_id":"%s","action":"execute","created_task_ids":"%s"}',
                        role,
                        cycle,
                        task_id,
                        ",".join(created_ids),
                    )
                elif role == "improve":
                    if action == "blocked_triage":
                        classification, created_ids = handle_blocked_triage(client, service, task_id)
                        known_triaged_blocked_ids.add(task_id)
                        logger.info(
                            '{"event":"watch_triage_complete","role":"%s","cycle":%d,"task_id":"%s","classification":"%s","created_task_ids":"%s"}',
                            role,
                            cycle,
                            task_id,
                            classification,
                            ",".join(created_ids),
                        )
                    else:
                        created_ids = handle_improve_task(client, service, task_id)
                        logger.info(
                            '{"event":"watch_improve_complete","role":"%s","cycle":%d,"task_id":"%s","created_task_ids":"%s"}',
                            role,
                            cycle,
                            task_id,
                            ",".join(created_ids),
                        )
                else:
                    raise ValueError(f"Unsupported worker role '{role}'")
        except ValueError as exc:
            logger.info(
                '{"event":"watch_no_task","role":"%s","cycle":%d,"message":"%s"}',
                role,
                cycle,
                str(exc).replace('"', "'"),
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            if response.status_code == 429:
                retry_after_header = response.headers.get("Retry-After", "").strip()
                retry_after = int(retry_after_header) if retry_after_header.isdigit() else 0
                backoff_seconds = max(poll_interval_seconds * RATE_LIMIT_BACKOFF_MULTIPLIER, retry_after)
                logger.info(
                    '{"event":"watch_rate_limited","role":"%s","cycle":%d,"status":429,"backoff_seconds":%d}',
                    role,
                    cycle,
                    backoff_seconds,
                )
                if max_cycles is not None and cycle >= max_cycles:
                    logger.info('{"event":"watch_complete","role":"%s","cycles":%d}', role, cycle)
                    return
                time.sleep(backoff_seconds)
                continue
            logger.info(
                '{"event":"watch_error","role":"%s","cycle":%d,"message":"%s"}',
                role,
                cycle,
                str(exc).replace('"', "'"),
            )
        except Exception as exc:
            logger.info(
                '{"event":"watch_error","role":"%s","cycle":%d,"message":"%s"}',
                role,
                cycle,
                str(exc).replace('"', "'"),
            )

        if max_cycles is not None and cycle >= max_cycles:
            logger.info('{"event":"watch_complete","role":"%s","cycles":%d}', role, cycle)
            return
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
