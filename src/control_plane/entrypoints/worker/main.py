from __future__ import annotations

import argparse
import html
import logging
import re
import time
from typing import Any

from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService
from control_plane.config import load_settings

TRIAGE_COMMENT_MARKER = "Blocked triage result"
IMPROVE_COMMENT_MARKER = "Improve worker result"


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


def select_ready_task_id(client: PlaneClient, ready_state: str = "Ready for AI", role: str = "goal") -> str:
    issues = client.list_issues()
    for issue in issues:
        task_id = str(issue["id"])
        status_name = issue_status_name(issue)
        task_kind = issue_task_kind(issue)
        if task_kind != role:
            continue
        if status_name == ready_state:
            return task_id
        if not status_name or status_name == str(issue.get("state", "")):
            detailed_issue = client.fetch_issue(task_id)
            if issue_task_kind(detailed_issue) == role and issue_status_name(detailed_issue) == ready_state:
                return task_id
    raise ValueError(f"No work item found in state '{ready_state}'")


def select_watch_candidate(client: PlaneClient, *, ready_state: str, role: str) -> tuple[str, str]:
    issues = client.list_issues()
    if role == "improve":
        for issue in issues:
            task_id = str(issue["id"])
            if issue_status_name(issue) != "Blocked":
                continue
            detailed_issue = client.fetch_issue(task_id)
            if issue_status_name(detailed_issue) == "Blocked" and not blocked_issue_already_triaged(client, task_id):
                return task_id, "blocked_triage"
        for issue in issues:
            task_id = str(issue["id"])
            if issue_status_name(issue) == ready_state and issue_task_kind(issue) == "improve":
                return task_id, "improve_task"
            detailed_issue = client.fetch_issue(task_id)
            if issue_status_name(detailed_issue) == ready_state and issue_task_kind(detailed_issue) == "improve":
                return task_id, "improve_task"
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


def handle_goal_task(client: PlaneClient, service: ExecutionService, task_id: str) -> None:
    service.run_task(client, task_id)


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
    created = create_follow_up_task(
        client,
        service,
        source_role="improve",
        task_kind="goal",
        original_issue=issue,
        title=f"Implement follow-up for {issue.get('name', 'improve task')}",
        goal_text=(
            f"Turn the improve task '{issue.get('name', 'Untitled')}' into a bounded implementation task and complete "
            "the described follow-up work."
        ),
        constraints_text=(
            f"- source_task_id: {task_id}\n"
            "- keep this follow-up bounded and implementation-focused"
        ),
    )
    client.comment_issue(
        task_id,
        "\n".join(
            [
                IMPROVE_COMMENT_MARKER,
                f"- follow_up_task_id: {created.get('id')}",
                f"- follow_up_title: {created.get('name')}",
                "- status: review",
            ]
        ),
    )
    client.transition_issue(task_id, "Review")
    return [str(created.get("id"))]


def handle_blocked_triage(client: PlaneClient, service: ExecutionService, task_id: str) -> tuple[str, list[str]]:
    issue = client.fetch_issue(task_id)
    comments = client.list_comments(task_id)
    classification, rationale = classify_blocked_issue(issue, comments)
    created_ids: list[str] = []

    if classification != "unknown/manual attention":
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

    while True:
        cycle += 1
        logger.info(
            '{"event":"watch_cycle_start","role":"%s","cycle":%d,"poll_interval_seconds":%d}',
            role,
            cycle,
            poll_interval_seconds,
        )
        try:
            task_id, action = select_watch_candidate(client, ready_state=ready_state, role=role)
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
                    handle_goal_task(client, service, task_id)
                    logger.info(
                        '{"event":"watch_action_complete","role":"%s","cycle":%d,"task_id":"%s","action":"execute","created_task_ids":""}',
                        role,
                        cycle,
                        task_id,
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
